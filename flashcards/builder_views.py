# flashcards/builder_views.py
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.db import models
from .ai import ingest, chunker, flashcard_gen
from .models import Deck, Card

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def generate_deck(request):
    """
    POST { "file_path": "...", "deck_name": "Audish" }
    """
    pdf_path  = request.data["file_path"]
    deck_name = request.data.get("deck_name", "Untitled")

    raw     = ingest.extract_text(pdf_path)
    chunks  = chunker.make_chunks(raw, max_tokens=700)
    cards   = flashcard_gen._cards_from_chunk("\n\n".join(chunks), 10)

    deck = Deck.objects.create(user=request.user, name=deck_name)
    for c in cards:
        Card.objects.create(deck=deck, front=c["front"], back=c["back"])
    return Response({"deck_id": deck.id, "cards": len(cards)})
