"""
F5: generation outcomes are coherent and errors are safe.

* Fewer cards than requested is a 201 that says so (`partial`, a first
  warning with the counts); a deck with no cards is never created.
* Model outages and empty output are 502 `generation_failed`; a missing
  key is 503 `generation_unavailable`; bugs are a generic 500. None of them
  leaks exception text, and no temporary file survives any of them.
"""
from __future__ import annotations

import json
import logging
import pathlib
from unittest import mock

import fitz
from django.test import override_settings

from flashcards.models import Card, Deck
from flashcards.tests.fakes import make_pdf
from flashcards.tests.test_pipeline import ANALYZE, GENERATE, UNEVEN_TOC, PipelineTestCase

PDF = make_pdf(6, toc=UNEVEN_TOC)  # Cells 1–4 (12 facts), Photosynthesis 5 (3), Respiration 6 (3)


class GenerationTestCase(PipelineTestCase):
    def gen(self, **fields):
        return self.generate(PDF, **fields)

    def assertNoTempFiles(self):
        self.assertEqual(list(pathlib.Path(self.tmpdir).iterdir()), [])

    def assertSafeError(self, res, status, code):
        self.assertEqual(res.status_code, status, res.content)
        body = res.json()
        self.assertEqual(body["code"], code)
        for leak in ("Traceback", "Error", self.tmpdir, "tmp", "\\", "boom", "fake.invalid"):
            self.assertNotIn(leak, body["detail"])
        self.assertNoTempFiles()
        return body


class PartialGenerationTests(GenerationTestCase):
    def test_full_success_is_not_partial(self):
        body = self.gen(allocations=self.allocations({"Cells": 2, "Photosynthesis": 1, "Respiration": 1})).json()
        self.assertEqual((body["cards_created"], body["requested"], body["partial"]), (4, 4, False))
        self.assertEqual(body["warnings"], [])

    def test_catch_up_fills_a_short_section_and_says_so(self):
        # Photosynthesis has 3 facts but 6 are asked for: catch-up makes up the
        # total under "Mixed topics", so the deck is complete but explained.
        res = self.gen(allocations=self.allocations({"Cells": 2, "Photosynthesis": 6, "Respiration": 1}))
        body = res.json()
        self.assertEqual(res.status_code, 201)
        self.assertEqual((body["cards_created"], body["requested"], body["partial"]), (9, 9, False))
        self.assertEqual(len(body["warnings"]), 2)
        self.assertTrue(body["warnings"][0].startswith('Section "Photosynthesis": requested 6, generated 3.'))
        self.assertIn('3 cards were added under "Mixed topics"', body["warnings"][1])
        per = {p["title"]: (p["planned"], p["created"]) for p in body["per_section"]}
        self.assertEqual(per, {"Cells": (2, 2), "Photosynthesis": (6, 3), "Respiration": (1, 1), "Mixed topics": (0, 3)})
        self.assertEqual(sum(p["created"] for p in body["per_section"]), body["cards_created"])

    def test_fewer_cards_than_requested_is_explicit(self):
        self.fake.empty_sections.add("Mixed topics")  # catch-up can't help
        res = self.gen(allocations=self.allocations({"Cells": 2, "Photosynthesis": 6, "Respiration": 1}))
        body = res.json()
        self.assertEqual(res.status_code, 201)
        self.assertEqual((body["cards_created"], body["requested"], body["partial"]), (6, 9, True))
        self.assertEqual(body["warnings"][0], "Created 6 of 9 requested cards.")
        self.assertEqual(Card.objects.filter(deck__public_id=body["deck_id"]).count(), 6)

    def test_a_failed_catch_up_keeps_the_cards_already_made(self):
        self.fake.fail.add("Mixed topics")
        body = self.gen(allocations=self.allocations({"Cells": 2, "Photosynthesis": 6, "Respiration": 1})).json()
        self.assertEqual((body["cards_created"], body["partial"]), (6, True))

    def test_without_a_plan_the_shortfall_is_reported_too(self):
        self.fake.empty_sections.add("Mixed topics")
        body = self.generate(make_pdf(1, facts_per_page=2), cards_wanted=5).json()
        self.assertEqual((body["cards_created"], body["requested"], body["partial"]), (2, 5, True))
        self.assertEqual(body["warnings"], ["Created 2 of 5 requested cards."])


class GenerationFailureTests(GenerationTestCase):
    def test_zero_cards_is_a_failure_and_creates_nothing(self):
        for title in ("Cells", "Photosynthesis", "Respiration", "Mixed topics"):
            self.fake.empty_sections.add(title)
        body = self.assertSafeError(self.gen(cards_wanted=6), 502, "generation_failed")
        self.assertIn("Nothing was saved", body["detail"])
        self.assertEqual((Deck.objects.count(), Card.objects.count()), (0, 0))

    def test_model_outage_is_a_502_not_a_success(self):
        self.fake.fail.update({"template", "cards"})
        self.assertSafeError(self.gen(cards_wanted=6), 502, "generation_failed")
        self.assertEqual(Deck.objects.count(), 0)

    def test_model_error_escaping_the_pipeline_is_a_502(self):
        import httpx
        import openai
        err = openai.APIConnectionError(request=httpx.Request("POST", "https://fake.invalid/v1"))
        with mock.patch("flashcards.views.cards_from_document", side_effect=err):
            self.assertSafeError(self.gen(cards_wanted=6), 502, "generation_failed")

    @override_settings(OPENAI_API_KEY="")
    def test_no_model_key_is_503_before_any_work(self):
        self.assertSafeError(self.gen(cards_wanted=6), 503, "generation_unavailable")
        self.assertEqual(self.fake.calls, [])
        # Everything that needs no model still works.
        self.assertEqual(self.analyze(PDF).status_code, 200)

    def test_unexpected_errors_are_a_generic_500_and_logged(self):
        def boom(path):
            doc = fitz.open(path)  # still open when the error escapes (Windows file lock)
            raise RuntimeError(f"boom at {path} with {doc.page_count} pages")

        with mock.patch("flashcards.views.analyze_document", boom), \
                self.assertLogs("flashcards.uploads", level="ERROR") as logs:
            body = self.assertSafeError(self.analyze(PDF), 500, "server_error")
        self.assertEqual(body, {"detail": "Something went wrong on the server. Try again in a moment.",
                                "code": "server_error"})
        self.assertIn("boom at", "\n".join(logs.output))  # the detail stays in the server log

    def test_5xx_with_debug_off_does_not_crash_error_reporting(self):
        # F0.5 found Django 5.0's AdminEmailHandler crashing on Python 3.14
        # for any 5xx with DEBUG off. The suite no longer patches it out.
        from django.conf import settings
        self.assertFalse(settings.DEBUG)
        self.fake.fail.update({"template", "cards"})
        self.assertEqual(self.gen(cards_wanted=6).status_code, 502)


class PlanValidationTests(GenerationTestCase):
    def test_invalid_plans_are_400(self):
        cases = {
            "{not json": "couldn't be read",
            json.dumps({"title": "Cells"}): "couldn't be read",
            json.dumps([{"title": "Cells", "cards": "many"}]): "couldn't be read",
            json.dumps(["Cells"]): "couldn't be read",
            self.allocations({"Cells": 0, "Photosynthesis": 0, "Respiration": 0}): "has no cards",
            json.dumps([{"title": "", "cards": 3}]): "has no cards",
        }
        for raw, phrase in cases.items():
            with self.subTest(raw=raw):
                body = self.assertSafeError(self.gen(allocations=raw), 400, "invalid_allocations")
                self.assertIn(phrase, body["detail"])
        self.assertEqual(Deck.objects.count(), 0)
        self.assertEqual(self.fake.calls, [])

    def test_more_than_thirty_cards_is_400(self):
        ranges = {f"S{i}": (i, i) for i in range(1, 6)}
        toc = [[1, t, ps] for t, (ps, _) in ranges.items()]
        res = self.generate(make_pdf(5, toc=toc),
                            allocations=self.allocations({t: 8 for t in ranges}, ranges))  # 40 cards
        body = self.assertSafeError(res, 400, "invalid_allocations")
        self.assertIn("more than 30", body["detail"])

    def test_long_names_are_trimmed_to_the_column_size(self):
        long_toc = [[1, "S" * 250, 1], [1, "Other", 2]]
        ranges = {"S" * 250: (1, 1), "Other": (2, 2)}
        res = self.generate(make_pdf(2, toc=long_toc), name="n" * 300 + ".pdf",
                            allocations=self.allocations({"S" * 250: 2, "Other": 1}, ranges))
        self.assertEqual(res.status_code, 201, res.content)
        deck = Deck.objects.get(public_id=res.json()["deck_id"])
        self.assertEqual(len(deck.name), 200)
        self.assertLessEqual(max(len(c.section or "") for c in deck.card_set.all()), 200)


class ErrorContractTests(GenerationTestCase):
    def test_wrong_method_is_json_405(self):
        res = self.client.get(ANALYZE)
        self.assertEqual(res.status_code, 405)
        self.assertEqual(res.json()["code"], "method_not_allowed")

    def test_unknown_url_is_json_404(self):
        res = self.client.get("/api/flashcards/nope/")
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json(), {"detail": "Not found.", "code": "not_found"})

    def test_disallowed_host_is_json_400(self):
        res = self.client.get("/api/flashcards/health/", HTTP_HOST="evil.example")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json(), {"detail": "Bad request.", "code": "bad_request"})

    def test_every_error_body_has_detail_and_code(self):
        responses = [
            self.client.post(ANALYZE, {}, format="multipart"),
            self.client.get("/api/flashcards/hand/"),
            self.client.get("/api/flashcards/toc/", {"deck_id": "missing-deck-id-000000"}),
            self.client.get(GENERATE),
        ]
        for res in responses:
            with self.subTest(status=res.status_code):
                body = res.json()
                self.assertGreaterEqual(res.status_code, 400)
                self.assertIsInstance(body["detail"], str)
                self.assertRegex(body["code"], r"^[a-z_]+$")
                self.assertEqual(res["Content-Type"], "application/json")
