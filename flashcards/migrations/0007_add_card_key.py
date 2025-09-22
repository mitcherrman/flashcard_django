# flashcards/migrations/0007_add_card_key.py
from django.db import migrations, models
import hashlib, re

_KEY_RE = re.compile(r"[^a-z0-9]+")
def build_card_key(front: str, back: str) -> str:
    base = f"{front} || {back}".lower()
    base = _KEY_RE.sub(" ", base)
    base = " ".join(base.split())
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:40]

def backfill_card_keys(apps, schema_editor):
    Card = apps.get_model("flashcards", "Card")
    batch = []
    for card in Card.objects.all().only("id", "front", "back"):
        k = build_card_key(card.front or "", card.back or "")
        # assign via update to avoid model methods/validators
        Card.objects.filter(id=card.id).update(card_key=k)

class Migration(migrations.Migration):

    dependencies = [
        ("flashcards", "0006_card_section"),  # adjust if your last good migration differs
    ]

    operations = [
        migrations.AddField(
            model_name="card",
            name="card_key",
            field=models.CharField(max_length=64, db_index=True, blank=True, default=""),
        ),
        migrations.RunPython(backfill_card_keys, reverse_code=migrations.RunPython.noop),
    ]
