from rest_framework import serializers
from .models import Card

class CardSerializer(serializers.ModelSerializer):
    """
    Sent to the React‑Native client.
    Only the fields the games need are exposed.
    """
    class Meta:
        model  = Card
        fields = ("id", "front", "back", "right", "wrong")
