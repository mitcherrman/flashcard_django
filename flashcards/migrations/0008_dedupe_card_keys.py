# flashcards/migrations/0008_dedupe_card_keys.py
from django.db import migrations
from django.db.models import Count

def dedupe_card_keys(apps, schema_editor):
    Card = apps.get_model("flashcards", "Card")

    # Find (deck_id, card_key) groups with more than 1 row
    dup_groups = (
        Card.objects
        .filter(card_key__gt="")  # non-empty keys only
        .values("deck_id", "card_key")
        .annotate(cnt=Count("id"))
        .filter(cnt__gt=1)
    )

    for g in dup_groups:
        deck_id = g["deck_id"]
        key = g["card_key"]
        ids = list(
            Card.objects
            .filter(deck_id=deck_id, card_key=key)
            .order_by("id")
            .values_list("id", flat=True)
        )
        # keep the first, delete the rest
        if len(ids) > 1:
            Card.objects.filter(id__in=ids[1:]).delete()

class Migration(migrations.Migration):

    dependencies = [
        ("flashcards", "0007_add_card_key"),
    ]

    operations = [
        migrations.RunPython(dedupe_card_keys, reverse_code=migrations.RunPython.noop),
    ]
