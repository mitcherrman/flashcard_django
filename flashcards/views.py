# flashcards/views.py
from __future__ import annotations

import logging, pathlib, random, tempfile
from typing import Any, Dict, List

from django.core.files.uploadedfile import UploadedFile
from django.db.models import F
from django.http import JsonResponse
from rest_framework import status
from rest_framework.decorators import api_view, parser_classes, permission_classes
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.permissions import AllowAny, IsAuthenticatedOrReadOnly
from rest_framework.response import Response

from .models import Card, Deck
from .serializers import CardSerializer
from .ai.analysis import analyze_document

import json

# IMPORTANT: use the global-target pipeline function
# If your package layout differs, adjust this import:
from .ai.pipeline.core import cards_from_document

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

    with tempfile.NamedTemporaryFile(delete=False,
                                     suffix=pathlib.Path(up.name).suffix) as tmp:
        for ch in up.chunks():
            tmp.write(ch)
    tmp_path = pathlib.Path(tmp.name)

    try:
        stats = analyze_document(tmp_path)
        return Response(stats, status=200)
    except Exception as e:
        log.exception("analyze failed")
        return Response({"detail": f"analyze failed: {e}"}, status=500)
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass

# ────────────────────────────────────────────────────────────────────────────
#  /api/flashcards/generate/  – upload a file → create deck
# ────────────────────────────────────────────────────────────────────────────
def _spread_even(total: int, pages: List[int]) -> Dict[int, int]:
    total = max(0, int(total))
    if not pages:
        return {}
    base, extra = divmod(total, len(pages))
    q = {}
    for i, p in enumerate(pages):
        q[p] = q.get(p, 0) + base + (1 if i < extra else 0)
    return q

@api_view(["POST"])
@permission_classes([AllowAny])
@parser_classes([MultiPartParser, FormParser])
def generate_deck(request):
    log.info("FILES: %s", request.FILES)
    up: UploadedFile | None = request.FILES.get("file")
    if up is None:
        return Response({"detail": "file field required"}, status=400)

    deck_name = request.POST.get("deck_name", up.name)

    try:
        cards_wanted = int(request.POST.get("cards_wanted", 12))
    except ValueError:
        cards_wanted = 12
    # You can raise this if you want bigger decks
    cards_wanted = max(3, min(cards_wanted, 60))

    # Parse optional per-section allocations from UI
    import json
    allocations = []
    if request.POST.get("allocations"):
        try:
            allocations = json.loads(request.POST["allocations"]) or []
        except Exception:
            allocations = []

    # Build per-page *per-section* quotas preserving UI order
    # Dict[int, List[Tuple[str,int]]], e.g. {1: [("Limits",2), ("Derivative",2), ...]}
    def _spread_even(total: int, pages: List[int]) -> Dict[int, int]:
        total = max(0, int(total))
        if not pages:
            return {}
        base, extra = divmod(total, len(pages))
        q = {}
        for i, p in enumerate(pages):
            q[p] = q.get(p, 0) + base + (1 if i < extra else 0)
        return q

    per_page_section_quotas: Dict[int, List[tuple[str, int]]] = {}
    for a in allocations:
        try:
            title = (a.get("title") or "").strip()
            c     = int(a.get("cards", 0))
            p1    = int(a.get("page_start", 0))
            p2    = int(a.get("page_end", 0))
            if title and c > 0 and p1 >= 1 and p2 >= p1:
                pages = list(range(p1, p2 + 1))
                spread = _spread_even(c, pages)  # per-page split for this section
                for pg in pages:
                    cnt = int(spread.get(pg, 0))
                    if cnt <= 0:
                        continue
                    per_page_section_quotas.setdefault(pg, []).append((title, cnt))
        except Exception:
            continue

    # If user gave a plan, total_cards comes from it; otherwise use cards_wanted
    plan_total = sum(cnt for lst in per_page_section_quotas.values() for _, cnt in lst) if per_page_section_quotas else None
    total_cards = max(3, min((plan_total or cards_wanted), 60))

    tmp_path = None
    try:
        # Save upload to a temp file
        with tempfile.NamedTemporaryFile(delete=False,
                                         suffix=pathlib.Path(up.name).suffix) as tmp:
            for chunk in up.chunks():
                tmp.write(chunk)
        tmp_path = pathlib.Path(tmp.name)
        log.info("Temp saved → %s (%s bytes)", tmp_path, tmp_path.stat().st_size)

        # Build cards (section-aware quotas). We disable autoguess caps when the user
        # gave explicit per-section quotas for reliability.
        from .ai.pipeline.core import cards_from_document
        cards = cards_from_document(
            tmp_path,
            total_cards=total_cards if not per_page_section_quotas else None,
            max_cards_per_chunk=30,
            max_tokens=500,
            per_page_section_quotas=per_page_section_quotas or None,
            autoguess_section_caps=False if per_page_section_quotas else True,
        )

        if not cards:
            raise RuntimeError("OpenAI returned zero cards (check API key / quota / model)")

        # Persist
        user_obj = request.user if request.user.is_authenticated else None
        deck = Deck.objects.create(user=user_obj, name=deck_name)

        from .ai.flashcard_gen import build_card_key

        objs, seen = [], set()
        for idx, c in enumerate(cards, start=1):
            k = c.get("card_key") or build_card_key(c.get("front",""), c.get("back",""))
            if k in seen:
                continue
            seen.add(k)
            objs.append(Card(
                deck=deck,
                front=c["front"],
                back=c["back"],
                excerpt=c.get("excerpt", "")[:500],
                page=c.get("page"),
                section=c.get("section") or None,
                context=c.get("context") or "",
                card_key=k,
                ordinal=idx,
            ))

        # Best-effort dedupe at DB layer
        from django.db.utils import IntegrityError
        try:
            Card.objects.bulk_create(objs, ignore_conflicts=True)
        except IntegrityError:
            pass

        return Response({"deck_id": deck.id, "cards_created": len(objs)}, status=201)

    except Exception as exc:
        log.exception("Deck build failed")
        return Response({"detail": f"Deck build failed: {exc!s}"}, status=500)
    finally:
        if tmp_path:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass

    
# ────────────────────────────────────────────────────────────────────────────
#  /api/flashcards/hand/  – return n random cards from a deck
# ────────────────────────────────────────────────────────────────────────────
@api_view(["GET"])
@permission_classes([IsAuthenticatedOrReadOnly])
def hand(request) -> Response:
    n_param = (request.GET.get("n") or "12").lower()
    deck_id = request.GET.get("deck_id")
    order   = (request.GET.get("order") or "").lower()

    qs = Card.objects.filter(deck_id=deck_id) if deck_id else Card.objects.all()
    if not qs.exists():
        return Response([], status=200)

    if order == "doc":
        qs = qs.order_by("ordinal", "page", "section", "id")
    else:
        qs = qs.order_by("?")

    cards = list(qs) if n_param in ("all", "0") else list(qs[:int(n_param or 12)])
    return Response(CardSerializer(cards, many=True).data)

# ────────────────────────────────────────────────────────────────────────────
#  /api/flashcards/feedback/  – increment right/wrong counters
# ────────────────────────────────────────────────────────────────────────────
@api_view(["POST"])
@permission_classes([IsAuthenticatedOrReadOnly])
def feedback(request) -> Response:
    data: dict[str, Any] = request.data or {}
    right_ids  = data.get("right", [])
    wrong_ids  = data.get("wrong", [])
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
