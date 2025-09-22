# flashcards/migrations/0009_add_unique_constraint.py
from django.db import migrations, models

class Migration(migrations.Migration):

    dependencies = [
        ("flashcards", "0008_dedupe_card_keys"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="card",
            constraint=models.UniqueConstraint(
                fields=["deck", "card_key"],
                name="uniq_card_by_key_per_deck",
            ),
        ),
    ]
