from rest_framework import serializers
from .models import Card

class CardSerializer(serializers.ModelSerializer):
    class Meta:
        model = Card
        fields = ["id", "deck", "front", "back",
                  "excerpt", "context", "page", "section",
                  "right", "wrong", "ordinal"]
