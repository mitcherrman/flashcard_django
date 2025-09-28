# flashcards/views.py
from __future__ import annotations

import json
import logging
import pathlib
import tempfile
from typing import Any, Dict, List, Optional

from django.core.files.uploadedfile import UploadedFile
from django.db import models
from django.db.models import F, Q
from django.http import JsonResponse
from rest_framework import status
from rest_framework.decorators import api_view, parser_classes, permission_classes
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.permissions import AllowAny, IsAuthenticatedOrReadOnly
from rest_framework.response import Response

from .models import Card, Deck
from .serializers import CardSerializer
from .ai.analysis import analyze_document
from .ai.pipeline.core import cards_from_document
from .ai.flashcard_gen import build_card_key

log = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────────────
#  /api/flashcards/analyze/  – quick document stats for the UI
# ────────────────────────────────────────────────────────────────────────────
@api_view(["POST"])
@permission_classes([AllowAny])
@parser_classes([MultiPartParser, FormParser])
def analyze(request):
    up = request.FILES.get("file")
    if not up:
        return Response({"detail": "file field required"}, status=400)

    tmp_path: Optional[pathlib.Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            delete=False, suffix=pathlib.Path(up.name).suffix
        ) as tmp:
            for ch in up.chunks():
                tmp.write(ch)
        tmp_path = pathlib.Path(tmp.name)

        stats = analyze_document(tmp_path)
        return Response(stats, status=200)
    except Exception as e:
        log.exception("analyze failed")
        return Response({"detail": f"analyze failed: {e}"}, status=500)
    finally:
        try:
            if tmp_path:
                tmp_path.unlink(missing_ok=True)
        except Exception:
            pass


# ────────────────────────────────────────────────────────────────────────────
# helpers
# ────────────────────────────────────────────────────────────────────────────
def _parse_allocations(raw: str | None) -> list[dict]:
    """
    Expect JSON like:
      [
        {"title":"A","page_start":1,"page_end":2,"cards":5},
        ...
      ]
    """
    if not raw:
        return []
    try:
        data = json.loads(raw) or []
        out = []
        for a in data:
            out.append(
                {
                    "title": (a.get("title") or "").strip(),
                    "page_start": int(a.get("page_start") or 1),
                    "page_end": int(a.get("page_end") or a.get("page_start") or 1),
                    "cards": int(a.get("cards") or 0),
                }
            )
        return out
    except Exception:
        return []


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


# ────────────────────────────────────────────────────────────────────────────
#  /api/flashcards/generate/  – upload a file → create a deck
# ────────────────────────────────────────────────────────────────────────────
@api_view(["POST"])
@permission_classes([AllowAny])
@parser_classes([MultiPartParser, FormParser])
def generate_deck(request):
    MAX_PER_SECTION = 8         # align with core.py default
    MAX_TOTAL       = 30

    up: UploadedFile | None = request.FILES.get("file")
    if up is None:
        return Response({"detail": "file field required"}, status=400)

    deck_name = request.POST.get("deck_name", up.name)

    # Desired total (fallback when no explicit per-section counts)
    try:
        cards_wanted = int(request.POST.get("cards_wanted", 12))
    except ValueError:
        cards_wanted = 12
    cards_wanted = max(3, min(cards_wanted, MAX_TOTAL))

    allocations = _parse_allocations(request.POST.get("allocations"))

    # clamp per-section requests and compute planned totals
    planned_by_title: dict[str, int] = {}
    if allocations:
        for a in allocations:
            title = (a.get("title") or "").strip()
            if not title:
                continue
            n = int(a.get("cards") or 0)
            planned_by_title[title] = max(0, min(n, MAX_PER_SECTION))

    total_cards = sum(planned_by_title.values()) if planned_by_title else cards_wanted
    total_cards = max(3, min(total_cards, MAX_TOTAL))

    tmp_path: Optional[pathlib.Path] = None
    try:
        # save upload to tmp
        with tempfile.NamedTemporaryFile(delete=False, suffix=pathlib.Path(up.name).suffix) as tmp:
            for ch in up.chunks():
                tmp.write(ch)
        tmp_path = pathlib.Path(tmp.name)

        # sections_plan for pipeline (respect per-section caps we just clamped)
        sections_plan = None
        if allocations:
            sections_plan = []
            for a in allocations:
                title = (a.get("title") or "").strip()
                if not title:
                    continue
                sections_plan.append({
                    "title": title,
                    "page_start": int(a.get("page_start") or 1),
                    "page_end": int(a.get("page_end") or 1),
                    "cards": planned_by_title.get(title, 0),
                })

        # build cards from the PDF — UPDATED call (matches new core.py)
        cards = cards_from_document(
            tmp_path,
            total_cards=total_cards,
            max_tokens=500,
            sections_plan=sections_plan,          # heading→next heading slices (templater)
            max_cards_per_section=MAX_PER_SECTION # hard cap per section
        )
        if not cards:
            raise RuntimeError("Model returned zero cards.")

        # persist deck + cards
        user_obj = request.user if request.user.is_authenticated else None
        deck = Deck.objects.create(user=user_obj, name=deck_name)

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
                    section=(c.get("section") or "").strip() or None,
                    context=(c.get("context") or "").strip()[:20],
                    card_key=k,
                    distractors=c.get("distractors") or [],
                )
            )
        Card.objects.bulk_create(objs, ignore_conflicts=True)

        # Optional: keep the warnings UI you already wired up
        warnings: list[str] = []
        per_section_actual: dict[str, int] = dict(
            Card.objects.filter(deck=deck)
            .values_list("section")
            .annotate(n=models.Count("id"))
            .values_list("section", "n")
        )
        for title, planned_n in planned_by_title.items():
            got = int(per_section_actual.get(title, 0))
            if got < planned_n:
                warnings.append(
                    f'Section "{title}": requested {planned_n}, generated {got}. '
                    "That section didn’t have enough distinct facts to make more."
                )

        created = Card.objects.filter(deck=deck).count()
        return Response(
            {
                "deck_id": deck.id,
                "cards_created": created,
                "requested": total_cards,
                "warnings": warnings,
                "per_section": [
                    {
                        "title": t,
                        "planned": planned_by_title.get(t, 0),
                        "created": int(per_section_actual.get(t, 0)),
                    }
                    for t in (planned_by_title.keys() or per_section_actual.keys())
                ],
            },
            status=201,
        )

    except Exception as exc:
        log.exception("Deck build failed")
        return Response({"detail": f"Deck build failed: {exc!s}"}, status=500)
    finally:
        try:
            if tmp_path:
                tmp_path.unlink(missing_ok=True)
        except Exception:
            pass


# ────────────────────────────────────────────────────────────────────────────
#  /api/flashcards/hand/  – fetch cards to study
#     query:
#       deck_id=…      (required)
#       n=all|<int>    (default 12)
#       order=doc|random (default random; doc = page asc, id asc)
#       start_ordinal=<int> (optional, rotates list for doc order)
# ────────────────────────────────────────────────────────────────────────────
@api_view(["GET"])
@permission_classes([IsAuthenticatedOrReadOnly])
def hand(request) -> Response:
    deck_id = request.GET.get("deck_id")
    if not deck_id:
        return Response({"detail": "deck_id required"}, status=400)

    n_raw = request.GET.get("n", "12")
    order = request.GET.get("order", "random")

    qs = Card.objects.filter(deck_id=deck_id)
    if not qs.exists():
        return Response([], status=200)

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
# ────────────────────────────────────────────────────────────────────────────
@api_view(["GET"])
@permission_classes([AllowAny])
def toc(request) -> Response:
    deck_id = request.GET.get("deck_id")
    if not deck_id:
        return Response({"detail": "deck_id required"}, status=400)

    qs = _stable_doc_ordering(Card.objects.filter(deck_id=deck_id))
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
#  /api/flashcards/feedback/  – increment right/wrong counters
# ────────────────────────────────────────────────────────────────────────────
@api_view(["POST"])
@permission_classes([IsAuthenticatedOrReadOnly])
def feedback(request) -> Response:
    data: dict[str, Any] = request.data or {}
    right_ids = data.get("right", [])
    wrong_ids = data.get("wrong", [])
    if right_ids:
        Card.objects.filter(id__in=right_ids).update(right=F("right") + 1)
    if wrong_ids:
        Card.objects.filter(id__in=wrong_ids).update(wrong=F("wrong") + 1)
    return Response({"ok": True})


# ────────────────────────────────────────────────────────────────────────────
#  /api/flashcards/health  – simple health check
# ────────────────────────────────────────────────────────────────────────────
@api_view(["GET"])
@permission_classes([])  # public
def health(_request):
    return JsonResponse({"ok": True})
