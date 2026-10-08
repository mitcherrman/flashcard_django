# flashcards/views.py
from __future__ import annotations

import json
import logging
import pathlib
from collections import Counter
from dataclasses import dataclass
from typing import Any

import openai
from django.conf import settings
from django.db import models, transaction
from django.http import JsonResponse
from rest_framework.decorators import api_view, parser_classes, permission_classes, throttle_classes
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .errors import ApiError
from .models import PUBLIC_ID_RE, Card, Deck
from .serializers import CardSerializer
from .throttles import AnalyzeThrottle, GenerateThrottle, ReadThrottle, consume_generation_budget
from .uploads import receive_pdf
from .ai.analysis import analyze_document
from .ai.pipeline.core import cards_from_document
from .ai.flashcard_gen import build_card_key

log = logging.getLogger(__name__)

MAX_PER_SECTION = 8         # align with core.py default
MAX_TOTAL       = 30
MIXED_TOPICS    = "Mixed topics"  # core.py's catch-up section


# ────────────────────────────────────────────────────────────────────────────
#  /api/flashcards/analyze/  – quick document stats for the UI (no model calls)
# ────────────────────────────────────────────────────────────────────────────
@api_view(["POST"])
@permission_classes([AllowAny])
@parser_classes([MultiPartParser, FormParser])
@throttle_classes([AnalyzeThrottle])
def analyze(request):
    stats = receive_pdf(request).process(analyze_document)
    return Response(stats, status=200)


# ────────────────────────────────────────────────────────────────────────────
# helpers
# ────────────────────────────────────────────────────────────────────────────
def _invalid_plan(detail: str) -> ApiError:
    return ApiError(400, "invalid_allocations", detail)


def _parse_allocations(raw: Any) -> list[dict]:
    """
    Expect JSON like:
      [
        {"title":"A","page_start":1,"page_end":2,"cards":5},
        ...
      ]
    Absent or empty → []. Anything unreadable is a 400, not a silent fallback.
    """
    if raw in (None, ""):
        return []
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(data, list):
            raise ValueError("not a list")
        out = []
        for a in data:
            if not isinstance(a, dict):
                raise ValueError("not an object")
            out.append(
                {
                    "title": str(a.get("title") or "").strip(),
                    "page_start": int(a.get("page_start") or 1),
                    "page_end": int(a.get("page_end") or a.get("page_start") or 1),
                    "cards": int(a.get("cards") or 0),
                }
            )
        return out
    except (TypeError, ValueError):
        raise _invalid_plan("The section plan couldn't be read.") from None


def _stable_doc_ordering(qs):
    """
    Prefer Page ASC (NULLs last) then ID ASC as a stable tie-breaker.
    """
    # Push NULL pages to the end
    nulls_last = models.Case(
        models.When(page__isnull=True, then=models.Value(1)),
        default=models.Value(0),
        output_field=models.IntegerField(),
    )
    return qs.order_by(nulls_last, "page", "id")


def _deck_from_query(request) -> Deck:
    """The deck named by ?deck_id=<public id>: 400 when absent, 404 when unknown."""
    ref = (request.GET.get("deck_id") or "").strip()
    if not ref:
        raise ApiError(400, "deck_id_required", "deck_id is required.")
    deck = Deck.objects.filter(public_id=ref).first() if PUBLIC_ID_RE.fullmatch(ref) else None
    if deck is None:
        raise ApiError(404, "deck_not_found", "This deck doesn't exist on the server.")
    return deck


def _card_rows(cards: list[dict], deck: Deck) -> list[Card]:
    """Unsaved Card rows: deduplicated, in document order, ordinals 1..N."""
    objs: list[Card] = []
    seen: set[str] = set()
    for c in cards:
        front = (c.get("front") or "").strip()
        back  = (c.get("back") or "").strip()
        if not front or not back:
            continue
        k = c.get("card_key") or build_card_key(front, back)
        if not k or k in seen:
            continue
        seen.add(k)
        objs.append(
            Card(
                deck=deck,
                front=front,
                back=back,
                excerpt=(c.get("excerpt") or "")[:500],
                page=int(c.get("page")) if isinstance(c.get("page"), int) else None,
                section=(c.get("section") or "").strip()[:200] or None,
                context=(c.get("context") or "").strip()[:20],
                card_key=k,
                distractors=c.get("distractors") or [],
            )
        )
    # Ordinal = 1-based position in document order, the same order that
    # hand?order=doc and toc use (page ASC, unknown pages last, then id).
    # The sort is stable, so ties keep pipeline (section) order, and ids
    # are assigned in this order too.
    objs.sort(key=lambda o: (o.page is None, o.page or 0))
    for i, o in enumerate(objs, start=1):
        o.ordinal = i
    return objs


def _plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def _generation_summary(planned_by_title: dict[str, int], rows: list[Card], requested: int) -> dict:
    """
    What was made versus what was asked for. Fewer cards than requested is
    still a success (201), but it is always said explicitly: `partial` is
    true and the first warning gives the created/requested counts.
    """
    created = len(rows)
    actual = Counter(r.section or "" for r in rows)
    warnings: list[str] = []
    if created < requested:
        warnings.append(f"Created {created} of {requested} requested cards.")
    for title, planned_n in planned_by_title.items():
        got = int(actual.get(title, 0))
        if got < planned_n:
            warnings.append(
                f'Section "{title}": requested {planned_n}, generated {got}. '
                "That section didn’t have enough distinct facts to make more."
            )
    mixed = int(actual.get(MIXED_TOPICS, 0)) if MIXED_TOPICS not in planned_by_title else 0
    if mixed:
        warnings.append(
            f'{mixed} {_plural(mixed, "card was", "cards were")} added under "{MIXED_TOPICS}" '
            "to make up the total. They draw on the whole document, so their page may be unknown."
        )

    if planned_by_title:
        titles = list(planned_by_title) + ([MIXED_TOPICS] if mixed else [])
    else:
        titles = list(dict.fromkeys(r.section or "" for r in rows))
    per_section = [
        {"title": t, "planned": planned_by_title.get(t, 0), "created": int(actual.get(t, 0))}
        for t in titles
    ]
    return {
        "cards_created": created,
        "requested": requested,
        "partial": created < requested,
        "warnings": warnings,
        "per_section": per_section,
    }


@dataclass(frozen=True)
class _GenerationPlan:
    deck_name: str
    planned_by_title: dict[str, int]    # clamped per-section counts ({} without a plan)
    sections_plan: list[dict] | None
    total_cards: int


def _generation_plan(request, upload_name: str) -> _GenerationPlan:
    deck_name = str(request.data.get("deck_name") or "").strip() or pathlib.Path(upload_name).stem
    deck_name = deck_name[:200] or "Untitled"

    # Desired total (fallback when no explicit per-section counts)
    try:
        cards_wanted = int(request.data.get("cards_wanted", 12))
    except (TypeError, ValueError):
        cards_wanted = 12
    cards_wanted = max(3, min(cards_wanted, MAX_TOTAL))

    # clamp per-section requests and compute planned totals
    allocations = _parse_allocations(request.data.get("allocations"))
    planned_by_title: dict[str, int] = {}
    sections_plan = None
    if allocations:
        sections_plan = []
        for a in allocations:
            title = a["title"]
            if not title:
                continue
            planned_by_title[title] = max(0, min(a["cards"], MAX_PER_SECTION))
            sections_plan.append({
                "title": title,
                "page_start": a["page_start"],
                "page_end": a["page_end"],
                "cards": planned_by_title[title],
            })
        planned_total = sum(planned_by_title.values())
        if planned_total == 0:
            raise _invalid_plan("The section plan has no cards.")
        if planned_total > MAX_TOTAL:
            raise _invalid_plan(f"The section plan asks for more than {MAX_TOTAL} cards.")

    total_cards = sum(planned_by_title.values()) if planned_by_title else cards_wanted
    total_cards = max(3, min(total_cards, MAX_TOTAL))
    return _GenerationPlan(deck_name, planned_by_title, sections_plan, total_cards)


def _run_pipeline(path: pathlib.Path, plan: _GenerationPlan):
    # Only a validated request reaches the model, and only then is it
    # counted against the server-wide daily budget.
    consume_generation_budget()
    try:
        return cards_from_document(
            path,
            total_cards=plan.total_cards,
            max_tokens=500,
            sections_plan=plan.sections_plan,
            max_cards_per_section=MAX_PER_SECTION,
            return_template=True,   # ← ask core to give us the LLM outline
            title=plan.deck_name,   # template title = deck name, not the temp-file stem
            cache_chunks=False,     # nothing reads the chunk cache
        )
    except openai.OpenAIError:
        log.exception("Deck build failed: model error")
    # Raised outside the except block, so the failed call's frames are gone
    # before the upload is deleted (see uploads.py).
    raise ApiError(502, "generation_failed",
                   "The card generator didn't respond. Nothing was saved. Try again in a moment.")


# ────────────────────────────────────────────────────────────────────────────
#  /api/flashcards/generate/  – upload a file → create a deck (model calls)
# ────────────────────────────────────────────────────────────────────────────
@api_view(["POST"])
@permission_classes([AllowAny])
@parser_classes([MultiPartParser, FormParser])
@throttle_classes([GenerateThrottle])
def generate_deck(request):
    if not settings.OPENAI_API_KEY:
        raise ApiError(503, "generation_unavailable", "Card generation isn't set up on this server.")

    pdf = receive_pdf(request)

    def build(path: pathlib.Path):
        plan = _generation_plan(request, pdf.name)
        return plan, _run_pipeline(path, plan)

    plan, (cards, template) = pdf.process(build)

    deck = Deck(name=plan.deck_name)
    rows = _card_rows(cards, deck)
    if not rows:
        log.warning("Deck build produced no cards (requested %s)", plan.total_cards)
        raise ApiError(502, "generation_failed",
                       "No cards could be generated from this document. Nothing was saved. "
                       "Try again, or try a different PDF.")
    with transaction.atomic():
        deck.save()
        for r in rows:
            r.deck = deck
        Card.objects.bulk_create(rows, ignore_conflicts=True)

    return Response(
        {
            "deck_id": deck.public_id,
            "template": template or {},
            **_generation_summary(plan.planned_by_title, rows, plan.total_cards),
        },
        status=201,
    )


# ────────────────────────────────────────────────────────────────────────────
#  /api/flashcards/hand/  – fetch cards to study
#     query:
#       deck_id=…      (required; the deck's public id)
#       n=all|<int>    (default 12)
#       order=doc|random (default random; doc = page asc, id asc)
#  404 deck_not_found for an unknown deck; 200 [] for a deck with no cards.
# ────────────────────────────────────────────────────────────────────────────
@api_view(["GET"])
@permission_classes([AllowAny])
@throttle_classes([ReadThrottle])
def hand(request) -> Response:
    deck = _deck_from_query(request)

    n_raw = request.GET.get("n", "12")
    order = request.GET.get("order", "random")

    qs = Card.objects.filter(deck=deck).select_related("deck")

    # Preserve canonical document order; no rotation here
    if order == "doc":
        qs = _stable_doc_ordering(qs)
    else:
        qs = qs.order_by("?")

    # n
    if n_raw == "all":
        cards_qs = qs
    else:
        try:
            n = max(1, min(int(n_raw), 200))
        except Exception:
            n = 12
        cards_qs = qs[:n]

    data = CardSerializer(list(cards_qs), many=True).data
    return Response(data)


# ────────────────────────────────────────────────────────────────────────────
#  /api/flashcards/toc/  – Table of Contents for a deck (doc order + ordinal)
#  404 deck_not_found for an unknown deck; 200 [] for a deck with no cards.
# ────────────────────────────────────────────────────────────────────────────
@api_view(["GET"])
@permission_classes([AllowAny])
@throttle_classes([ReadThrottle])
def toc(request) -> Response:
    deck = _deck_from_query(request)

    qs = _stable_doc_ordering(Card.objects.filter(deck=deck))
    items = []
    for i, c in enumerate(qs, start=1):
        items.append(
            {
                "id": c.id,
                "ordinal": i,
                "front": c.front,
                "section": c.section or "",
                "page": c.page,
                "context": c.context or "",
            }
        )
    return Response(items, status=200)


# ────────────────────────────────────────────────────────────────────────────
#  /api/flashcards/health  – simple health check
# ────────────────────────────────────────────────────────────────────────────
@api_view(["GET"])
@permission_classes([])  # public
def health(_request):
    return JsonResponse({"ok": True})
