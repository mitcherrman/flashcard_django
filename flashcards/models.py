# flashcards/models.py
from django.db import models
from django.contrib.auth import get_user_model
User = get_user_model()

class Deck(models.Model):
    user = models.ForeignKey(          # ← add null=True, blank=True
        User, on_delete=models.CASCADE, null=True, blank=True
    )
    name    = models.CharField(max_length=200)
    created = models.DateTimeField(auto_now_add=True)

class Card(models.Model):
    deck  = models.ForeignKey(Deck, on_delete=models.CASCADE)
    front = models.TextField()
    back  = models.TextField()
    right = models.IntegerField(default=0)
    wrong = models.IntegerField(default=0)
