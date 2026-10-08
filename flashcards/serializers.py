from rest_framework import serializers
from .models import Card

class CardSerializer(serializers.ModelSerializer):
    # The deck's public id (the integer key is internal; see models.Deck).
    deck = serializers.CharField(source="deck.public_id", read_only=True)

    class Meta:
        model = Card
        # right/wrong are kept for response compatibility. Nothing updates
        # them since F5 removed /feedback/, so they are always 0.
        fields = ["id", "deck", "front", "back",
                  "excerpt", "context", "page", "section",
                  "right", "wrong", "ordinal", "distractors"]
