# flashcards/views.py
from __future__ import annotations
import pathlib, tempfile, logging, random

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, IsAuthenticatedOrReadOnly
from rest_framework.response import Response
from rest_framework import status

from django.db.models import F
from django.core.files.uploadedfile import UploadedFile

from .models import Deck, Card
from .serializers import CardSerializer
from .ai.pipeline import core                           # ← new clean library

log = logging.getLogger(__name__)


# ------------------------------------------------------------------ #
# 1.  Upload a deposition & generate cards (Game‑agnostic)           #
# ------------------------------------------------------------------ #
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def generate_deck(request):
    """
    POST multipart/form‑data:
      file        – PDF / DOCX / TXT
      deck_name   – optional

    Response: { deck_id, cards_created }
    """
    up: UploadedFile = request.FILES.get("file")
    if not up:
        return Response({"detail": "file field required"},
                        status=status.HTTP_400_BAD_REQUEST)

    deck_name = request.POST.get("deck_name", up.name)
    # --- save upload to tmp and run pipeline ------------------------
    with tempfile.NamedTemporaryFile(delete=False, suffix=pathlib.Path(up.name).suffix) as tmp:
        for chunk in up.chunks():
            tmp.write(chunk)
        tmp_path = pathlib.Path(tmp.name)

    cards = core.cards_from_document(tmp_path, cards_per_chunk=3)
    log.info("Generated %s cards from %s", len(cards), up.name)

    deck = Deck.objects.create(user=request.user, name=deck_name)
    Card.objects.bulk_create([
        Card(deck=deck, front=c["front"], back=c["back"])
        for c in cards
    ])
    return Response({"deck_id": deck.id, "cards_created": len(cards)})


# ------------------------------------------------------------------ #
# 2.  Hand endpoint for the RN client                                #
# ------------------------------------------------------------------ #
@api_view(["GET"])
@permission_classes([IsAuthenticatedOrReadOnly])
def hand(request):
    """
    GET /api/flashcards/hand?deck_id=123&n=12
    Returns up to *n* random cards.
    If deck_id is omitted → random across all cards (demo).
    """
    n       = int(request.GET.get("n", 12))
    deck_id = request.GET.get("deck_id")

    qs = Card.objects.filter(deck_id=deck_id) if deck_id else Card.objects.all()
    if not qs.exists():
        return Response([], status=200)

    sample = random.sample(list(qs), min(n, qs.count()))
    return Response(CardSerializer(sample, many=True).data)


# ------------------------------------------------------------------ #
# 3.  Feedback from the games                                        #
# ------------------------------------------------------------------ #
@api_view(["POST"])
@permission_classes([IsAuthenticatedOrReadOnly])
def feedback(request):
    """
    React‑Native sends:

    {
      "right":  [card_id, …],     # answered correctly
      "wrong":  [card_id, …],     # answered incorrectly
      "kept":   [...],            # Game 1 keep
      "tossed": [...]
    }
    Only the keys relevant to the current game are sent.
    """
    data   = request.data or {}
    right  = data.get("right", [])
    wrong  = data.get("wrong", [])

    if right:
        Card.objects.filter(id__in=right).update(right=F("right") + 1)
    if wrong:
        Card.objects.filter(id__in=wrong).update(wrong=F("wrong") + 1)

    log.debug("Feedback received: %s", data)
    return Response({"ok": True})
