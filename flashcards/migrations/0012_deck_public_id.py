# F5: opaque public deck ids. Existing decks receive a fresh random id; the
# integer primary key is unchanged but no longer accepted by the API.

import secrets

from django.db import migrations, models

import flashcards.models


def fill_public_ids(apps, schema_editor):
    Deck = apps.get_model("flashcards", "Deck")
    for deck in Deck.objects.filter(public_id__isnull=True).only("id"):
        deck.public_id = secrets.token_urlsafe(16)
        deck.save(update_fields=["public_id"])


class Migration(migrations.Migration):

    dependencies = [
        ('flashcards', '0011_card_distractors'),
    ]

    operations = [
        migrations.AddField(
            model_name='deck',
            name='public_id',
            field=models.CharField(editable=False, max_length=32, null=True),
        ),
        migrations.RunPython(fill_public_ids, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='deck',
            name='public_id',
            field=models.CharField(default=flashcards.models.new_public_id, editable=False, max_length=32, unique=True),
        ),
    ]
