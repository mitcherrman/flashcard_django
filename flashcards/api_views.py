from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticatedOrReadOnly
from rest_framework.response import Response
from .models import Card, Deck
from .serializers import CardSerializer
import random, json

@api_view(["GET"])
@permission_classes([IsAuthenticatedOrReadOnly])
def hand(request):
    """
    GET /api/flashcards/hand?deck_id=7&n=12
    Returns up to *n* random cards from the deck.
    """
    deck_id = request.GET.get("deck_id")
    n       = int(request.GET.get("n", 10))

    qs = Card.objects.filter(deck_id=deck_id) if deck_id else Card.objects.all()
    cards = random.sample(list(qs), min(n, qs.count()))
    return Response(CardSerializer(cards, many=True).data)

@api_view(["POST"])
@permission_classes([IsAuthenticatedOrReadOnly])
def feedback(request):
    """
    POST body: { "kept": [1,2], "tossed": [3,4] }
    Very small demo – just prints to console for now.
    """
    data = json.loads(request.body or "{}")
    print("⚙️  feedback:", data)
    return Response({"status": "ok"})
