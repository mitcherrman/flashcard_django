from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()


class Deck(models.Model):
    user    = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)
    name    = models.CharField(max_length=200)
    created = models.DateTimeField(auto_now_add=True)


class Card(models.Model):
    deck  = models.ForeignKey(Deck, on_delete=models.CASCADE)
    front = models.TextField()
    back  = models.TextField()

    # extra metadata used by the UI
    excerpt = models.TextField(blank=True, default="")
    context = models.CharField(max_length=20, blank=True, default="")
    page    = models.PositiveIntegerField(null=True, blank=True)
    section = models.CharField(max_length=200, null=True, blank=True)

    # per-card performance (optional)
    right = models.IntegerField(default=0)
    wrong = models.IntegerField(default=0)

    # LLM-provided distractors for MC mode
    distractors = models.JSONField(default=list, blank=True)

    # dedupe + stable ordering
    card_key = models.CharField(max_length=64, db_index=True, blank=True, default="")
    ordinal  = models.PositiveIntegerField(default=0, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["deck", "card_key"], name="uniq_card_by_key_per_deck"),
            # When all rows have a proper ordinal, you may enable this:
            # models.UniqueConstraint(fields=["deck", "ordinal"], name="uniq_card_seq_per_deck"),
        ]
