"""
F5: decks are addressed by an opaque public id, unknown decks are a 404
(distinct from a deck with no cards), and /feedback/ is gone.
"""
from __future__ import annotations

import re

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from flashcards.models import Card, Deck
from flashcards.tests.fakes import make_pdf
from flashcards.tests.test_pipeline import UNEVEN_TOC, PipelineTestCase

HAND = "/api/flashcards/hand/"
TOC = "/api/flashcards/toc/"
PUBLIC_ID = re.compile(r"^[A-Za-z0-9_-]{22}$")


class DeckAccessTests(PipelineTestCase):
    def build(self) -> str:
        res = self.generate(make_pdf(6, toc=UNEVEN_TOC), cards_wanted=6)
        self.assertEqual(res.status_code, 201, res.content)
        return res.json()["deck_id"]

    def get(self, url, deck_id, **extra):
        params = {} if deck_id is None else {"deck_id": deck_id}
        params.update(extra)
        return self.client.get(url, params)

    def test_generate_returns_an_opaque_public_id(self):
        deck_id = self.build()
        self.assertIsInstance(deck_id, str)
        self.assertRegex(deck_id, PUBLIC_ID)
        deck = Deck.objects.get(public_id=deck_id)
        self.assertNotEqual(deck_id, str(deck.pk))

    def test_public_id_opens_the_deck(self):
        deck_id = self.build()
        hand = self.get(HAND, deck_id, n="all", order="doc")
        toc = self.get(TOC, deck_id)
        self.assertEqual((hand.status_code, toc.status_code), (200, 200))
        self.assertEqual(len(hand.json()), 6)
        self.assertEqual([t["id"] for t in toc.json()], [c["id"] for c in hand.json()])
        # Each card names its deck by the same public id, never the integer key.
        self.assertEqual({c["deck"] for c in hand.json()}, {deck_id})

    def test_sequential_integer_ids_do_not_open_decks(self):
        self.build()
        self.build()
        for deck in Deck.objects.all():
            for url in (HAND, TOC):
                res = self.get(url, deck.pk)
                self.assertEqual(res.status_code, 404, (url, deck.pk))
                self.assertEqual(res.json()["code"], "deck_not_found")

    def test_unknown_and_malformed_ids_are_404(self):
        self.build()
        for bad in ("A" * 22, "not-a-real-deck-id-000", "1", "../../etc/passwd", "x" * 200, "' OR 1=1 --"):
            for url in (HAND, TOC):
                res = self.get(url, bad)
                self.assertEqual(res.status_code, 404, (url, bad))
                self.assertEqual(res.json(), {"detail": "This deck doesn't exist on the server.",
                                              "code": "deck_not_found"})

    def test_missing_deck_id_is_400(self):
        for url in (HAND, TOC):
            for value in (None, "", "   "):
                res = self.get(url, value)
                self.assertEqual(res.status_code, 400)
                self.assertEqual(res.json()["code"], "deck_id_required")

    def test_existing_deck_without_cards_is_an_explicit_empty_200(self):
        deck = Deck.objects.create(name="empty")
        for url in (HAND, TOC):
            res = self.get(url, deck.public_id, n="all", order="doc")
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.json(), [])

    def test_deleted_deck_becomes_404(self):
        deck_id = self.build()
        Deck.objects.filter(public_id=deck_id).delete()
        self.assertEqual(Card.objects.count(), 0)
        for url in (HAND, TOC):
            self.assertEqual(self.get(url, deck_id).status_code, 404)

    def test_public_ids_are_unique_per_deck(self):
        ids = {self.build() for _ in range(5)}
        self.assertEqual(len(ids), 5)


class FeedbackRemovedTests(PipelineTestCase):
    def test_feedback_endpoint_is_gone(self):
        for method in ("get", "post"):
            res = getattr(self.client, method)("/api/flashcards/feedback/", {"right": [1]}, format="json")
            self.assertEqual(res.status_code, 404)
            self.assertEqual(res.json()["code"], "not_found")

    def test_counters_are_unchanged_and_still_reported(self):
        res = self.generate(make_pdf(6, toc=UNEVEN_TOC), cards_wanted=3)
        hand = self.client.get(HAND, {"deck_id": res.json()["deck_id"], "n": "all", "order": "doc"}).json()
        self.assertEqual({(c["right"], c["wrong"]) for c in hand}, {(0, 0)})


class PublicIdMigrationTests(TransactionTestCase):
    """0012 gives decks created before F5 a unique public id."""

    before = [("flashcards", "0011_card_distractors")]
    after = [("flashcards", "0012_deck_public_id")]

    def test_existing_decks_get_unique_public_ids(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.before)
        old_apps = executor.loader.project_state(self.before).apps
        OldDeck = old_apps.get_model("flashcards", "Deck")
        for i in range(3):
            OldDeck.objects.create(name=f"legacy {i}")

        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(self.after)
        NewDeck = executor.loader.project_state(self.after).apps.get_model("flashcards", "Deck")
        ids = list(NewDeck.objects.values_list("public_id", flat=True))
        self.assertEqual(len(ids), 3)
        self.assertEqual(len(set(ids)), 3)
        for pid in ids:
            self.assertRegex(pid, PUBLIC_ID)

        # Leave the schema at the latest migration for the other tests.
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())
