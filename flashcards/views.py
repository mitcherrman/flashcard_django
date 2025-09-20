# flashcards/views.py
from __future__ import annotations

import logging, pathlib, random, tempfile
from typing import Any

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
@api_view(["POST"])
@permission_classes([AllowAny])                  # keep public for now
def generate_deck(request):
    log.info("FILES: %s", request.FILES)

    up: UploadedFile | None = request.FILES.get("file")
    if up is None:
        return Response({"detail": "file field required"}, status=400)

    deck_name = request.POST.get("deck_name", up.name)

    try:
        # slider clamp (3–30)
        try:
            cards_wanted = int(request.POST.get("cards_wanted", 30))
        except ValueError:
            cards_wanted = 30
        cards_wanted = max(3, min(cards_wanted, 30))

        # 1) save upload to temp file
        with tempfile.NamedTemporaryFile(delete=False,
                                         suffix=pathlib.Path(up.name).suffix) as tmp:
            for chunk in up.chunks():
                tmp.write(chunk)
        tmp_path = pathlib.Path(tmp.name)
        log.info("Temp saved → %s (%s bytes)", tmp_path, tmp_path.stat().st_size)

        # 2) generate cards
        cards = cards_from_document(
            tmp_path,
            total_cards=cards_wanted,
            max_cards_per_chunk=min(3, cards_wanted),
            max_tokens=500,
        )

        # Fallback pad (rare)
        if len(cards) < cards_wanted and len(cards) > 0:
            need = cards_wanted - len(cards)
            cards = cards + (cards * (need // len(cards))) + cards[: need % len(cards)]
        cards = cards[:cards_wanted]

        if not cards:
            raise RuntimeError("OpenAI returned zero cards (check API key / quota / model name)")

        # 2.5) If a card has a page but no section, infer section from PDF TOC
        try:
            import fitz  # PyMuPDF
            doc = fitz.open(tmp_path.as_posix())
            pages = doc.page_count
            toc = doc.get_toc() or []           # [[level, title, page], ...] (page is 1-based)
            # build page→section map
            flat = [{"title": t, "page_start": p} for (lvl, t, p) in toc if 1 <= p <= pages]
            flat.sort(key=lambda x: x["page_start"])
            ranges = []
            for i, s in enumerate(flat):
                start = s["page_start"]
                end   = (flat[i+1]["page_start"] - 1) if i + 1 < len(flat) else pages
                ranges.append((start, end, s["title"]))
            doc.close()

            def section_for_page(pg: int | None) -> str | None:
                if not pg:
                    return None
                for a, b, title in ranges:
                    if a <= pg <= b:
                        return title
                return None

            for c in cards:
                if not c.get("section"):
                    c["section"] = section_for_page(c.get("page"))
        except Exception as _e:
            # non-fatal: if TOC missing or PyMuPDF not available, we just skip
            pass

        # 3) insert into DB  ⬇⬇⬇  (this is the block you asked about)
        user_obj = request.user if request.user.is_authenticated else None
        deck = Deck.objects.create(user=user_obj, name=deck_name)

        Card.objects.bulk_create([
            Card(
                deck=deck,
                front=c["front"],
                back=c["back"],
                excerpt=c.get("excerpt", "")[:500],
                page=c.get("page"),
                section=c.get("section") or None,    # NEW
                context=c.get("context") or None,    # NEW
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
