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
    cards_wanted = max(3, min(cards_wanted, 30))

    # Parse optional per-section allocations
    allocations = []
    if request.POST.get("allocations"):
        try:
            allocations = json.loads(request.POST["allocations"]) or []
        except Exception:
            allocations = []

    # Build per-page quotas from allocations
    per_page_quotas: Dict[int, int] = {}
    for a in allocations:
        try:
            c   = int(a.get("cards", 0))
            p1  = int(a.get("page_start", 0))
            p2  = int(a.get("page_end", 0))
            if c > 0 and p1 >= 1 and p2 >= p1:
                pages = list(range(p1, p2 + 1))
                chunk = _spread_even(c, pages)
                for pg, q in chunk.items():
                    per_page_quotas[pg] = per_page_quotas.get(pg, 0) + q
        except Exception:
            continue

    total_from_plan = sum(per_page_quotas.values()) if per_page_quotas else None
    total_cards = max(3, min((total_from_plan or cards_wanted), 30))

    try:
        with tempfile.NamedTemporaryFile(delete=False,
                                         suffix=pathlib.Path(up.name).suffix) as tmp:
            for chunk in up.chunks():
                tmp.write(chunk)
        tmp_path = pathlib.Path(tmp.name)
        log.info("Temp saved → %s (%s bytes)", tmp_path, tmp_path.stat().st_size)

        cards = cards_from_document(
            tmp_path,
            total_cards=total_cards,
            max_cards_per_chunk=30,
            max_tokens=500,
            per_page_quotas=per_page_quotas or None,
        )

        if not cards:
            raise RuntimeError("OpenAI returned zero cards (check API key / quota / model name)")

        # Annotate section from allocations (by page range), if present
        def section_for_page(p: int) -> str | None:
            for a in allocations:
                try:
                    if int(a.get("page_start", 0)) <= p <= int(a.get("page_end", 0)):
                        return str(a.get("title") or "").strip() or None
                except Exception:
                    continue
            return None

        for c in cards:
            p = c.get("page")
            if p is not None and "section" not in c:
                c["section"] = section_for_page(int(p))

        user_obj = request.user if request.user.is_authenticated else None
        deck = Deck.objects.create(user=user_obj, name=deck_name)
        Card.objects.bulk_create([
            Card(
                deck=deck,
                front=c["front"],
                back=c["back"],
                excerpt=c.get("excerpt", "")[:500],
                page=c.get("page"),
                section=c.get("section") or None,
                context=c.get("context") or "",
            )
            for c in cards
        ])

        return Response({"deck_id": deck.id, "cards_created": len(cards)}, status=201)

    except Exception as exc:
        log.exception("Deck build failed")
        return Response({"detail": f"Deck build failed: {exc!s}"},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
# ────────────────────────────────────────────────────────────────────────────
#  /api/flashcards/hand/  – return n random cards from a deck
# ────────────────────────────────────────────────────────────────────────────
@api_view(["GET"])
@permission_classes([IsAuthenticatedOrReadOnly])
def hand(request) -> Response:
    n       = int(request.GET.get("n", 12) or 12)
    deck_id = request.GET.get("deck_id")

    qs = Card.objects.filter(deck_id=deck_id) if deck_id else Card.objects.all()
    if not qs.exists():
        return Response([], status=200)

    sample = list(qs.order_by("?")[:n])  # efficient enough for small decks
    return Response(CardSerializer(sample, many=True).data)

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
