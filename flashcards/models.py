import re
import secrets

from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()

# A deck's public id: 128 random bits, URL-safe (22 characters). The API
# only ever accepts and returns this; the integer primary key stays internal,
# so decks can't be found by counting.
PUBLIC_ID_RE = re.compile(r"[A-Za-z0-9_-]{16,32}")


def new_public_id() -> str:
    return secrets.token_urlsafe(16)


class Deck(models.Model):
    user    = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)
    name    = models.CharField(max_length=200)
    created = models.DateTimeField(auto_now_add=True)
    public_id = models.CharField(max_length=32, unique=True, editable=False, default=new_public_id)


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
