# flashcards/views.py
from __future__ import annotations

import logging
import pathlib
import random
import tempfile
from typing import Any

from django.core.files.uploadedfile import UploadedFile
from django.db.models import F
from django.http import JsonResponse
from rest_framework import status
from rest_framework.decorators import (
    api_view,
    parser_classes,
    permission_classes,
)
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated, IsAuthenticatedOrReadOnly, AllowAny
from rest_framework.response import Response

from .ai.pipeline import core
from .models import Card, Deck
from .serializers import CardSerializer

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# 1)  /api/flashcards/generate/  – upload a file → create deck                #
# --------------------------------------------------------------------------- #
# flashcards/views.py   (replace generate_deck with this version)
# --------------------------------------------------------------------------- #
# 1)  /api/flashcards/generate/  – upload a file → create deck                #
# --------------------------------------------------------------------------- #
from traceback import print_exc                  #  ← NEW

@api_view(["POST"])
@permission_classes([AllowAny])                  # keep public for now
def generate_deck(request):
    """
    POST multipart/form-data  { file, deck_name? }

    Success → 201 { deck_id, cards_created }
    Failure → 4xx / 5xx  with { detail }
    """
    log.info("FILES: %s", request.FILES)

    up: UploadedFile | None = request.FILES.get("file")
    if up is None:
        return Response({"detail": "file field required"}, status=400)

    deck_name = request.POST.get("deck_name", up.name)

    try:
        cards_wanted = int(request.POST.get("cards_wanted", 12))
        cards_wanted = max(3, min(cards_wanted, 30))  # clamp 3‑30
    except ValueError:
        cards_wanted = 12

    try:
        # 1) save the upload to a temp file ---------------------------------
        with tempfile.NamedTemporaryFile(delete=False,
                                         suffix=pathlib.Path(up.name).suffix) as tmp:
            for chunk in up.chunks():
                tmp.write(chunk)
        tmp_path = pathlib.Path(tmp.name)
        log.info("Temp saved → %s (%s bytes)", tmp_path, tmp_path.stat().st_size)

        # 2) GPT → cards ----------------------------------------------------
        cards = core.cards_from_document(
            tmp_path,
            total_cards=cards_wanted,                   # ← from POST
            max_cards_per_chunk=min(3, cards_wanted),   # per-page ceiling
            max_tokens=500,
            sample_chunks=(None),              # keep your test mode, optional
            )


        if len(cards) >= cards_wanted:
            cards = cards[:cards_wanted]
        else:
            cards = cards * (cards_wanted // len(cards)) + cards[: cards_wanted % len(cards)]

        if not cards:
            raise RuntimeError(
                "OpenAI returned zero cards "
                "(check API key / quota / model name)"
            )        

        if not cards:
            raise RuntimeError("OpenAI returned zero cards "
                               "(check API key / quota / model name)")

        # 3) insert into DB -------------------------------------------------
        user_obj = request.user if request.user.is_authenticated else None
        deck = Deck.objects.create(user=user_obj, name=deck_name)
        Card.objects.bulk_create(
            [
                Card(
                    deck=deck,
                    front=c["front"],
                    back=c["back"],
                    excerpt=c.get("excerpt", "")[:500],
                    page=c.get("page"),                
                )
                for c in cards
            ]
        )

        return Response({"deck_id": deck.id,
                         "cards_created": len(cards)}, status=201)

    except Exception as exc:
        log.exception("Deck build failed")
        return Response(
            {"detail": f"Deck build failed: {exc!s}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )




# --------------------------------------------------------------------------- #
# 2)  /api/flashcards/hand/  – return *n* random cards for a deck             #
# --------------------------------------------------------------------------- #
@api_view(["GET"])
@permission_classes([IsAuthenticatedOrReadOnly])
def hand(request) -> Response:
    """
    GET parameters:

      deck_id   – optional; if absent, sample across *all* cards (demo)
      n         – optional, default = 12
    """
    n       = int(request.GET.get("n", 12))
    deck_id = request.GET.get("deck_id")

    qs = Card.objects.filter(deck_id=deck_id) if deck_id else Card.objects.all()
    if not qs.exists():
        return Response([], status=200)

    sample = random.sample(list(qs), min(n, qs.count()))
    return Response(CardSerializer(sample, many=True).data)


# --------------------------------------------------------------------------- #
# 3)  /api/flashcards/feedback/  – increment right / wrong counters           #
# --------------------------------------------------------------------------- #
@api_view(["POST"])
@permission_classes([IsAuthenticatedOrReadOnly])
def feedback(request) -> Response:
    """
    JSON body (any keys you need):

        {
          "right":  [ card_id, … ],
          "wrong":  [ ... ],
          "kept":   [ ... ],      # for Game 1
          "tossed": [ ... ]
        }
    """
    data: dict[str, Any] = request.data or {}
    right_ids  = data.get("right", [])
    wrong_ids  = data.get("wrong", [])

    if right_ids:
        Card.objects.filter(id__in=right_ids).update(right=F("right") + 1)
    if wrong_ids:
        Card.objects.filter(id__in=wrong_ids).update(wrong=F("wrong") + 1)

    log.debug("✅ feedback %s", data)
    return Response({"ok": True})


# --------------------------------------------------------------------------- #
# 4)  /api/flashcards/health  – simple health‑check (no auth)                 #
# --------------------------------------------------------------------------- #
@api_view(["GET"])
@permission_classes([])                     # public
def health(_request):
    return JsonResponse({"ok": True})


# flashcards/views.py (add imports if missing)
import re
import tempfile
from pathlib import Path
import fitz  # PyMuPDF

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

@api_view(["POST"])
@permission_classes([AllowAny])   # keep public; it's only metadata
def inspect_upload(request):
    """
    POST multipart/form-data { file }
    → 200 { ok, pages, words, chars, bytes, avg_words_per_page }
    Quick, non-blocking inspection; no DB writes.
    """
    up = request.FILES.get("file")
    if not up:
        return Response({"detail": "file field required"}, status=400)

    # Save to a temp file (PyMuPDF needs a real path)
    with tempfile.NamedTemporaryFile(delete=False,
                                     suffix=Path(up.name).suffix) as tmp:
        for chunk in up.chunks():
            tmp.write(chunk)
    p = Path(tmp.name)

    try:
        doc = fitz.open(p.as_posix())
        pages = doc.page_count

        words = 0
        chars = 0
        word_rx = re.compile(r"\b[\w’'-]+\b", re.UNICODE)  # count words reasonably

        for pg in doc:
            txt = pg.get_text("text") or ""
            chars += len(txt)
            words += len(word_rx.findall(txt))

        doc.close()

        b = p.stat().st_size
        avg_w = round(words / pages, 1) if pages else 0.0

        return Response({
            "ok": True,
            "pages": pages,
            "words": words,
            "chars": chars,
            "bytes": b,
            "avg_words_per_page": avg_w,
        })

    except Exception as e:
        return Response({"ok": False, "detail": str(e)}, status=500)
    finally:
        try:
            p.unlink(missing_ok=True)
        except Exception:
            pass
