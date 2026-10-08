"""
F0.5 regression suite: analysis → template → generation → persistence.

Synthetic PDFs only, a fake model (see fakes.py), the Django test database,
and no network: any attempt to reach a real HTTP endpoint fails the test.
"""
from __future__ import annotations

import pathlib
import shutil
import tempfile
from unittest import mock

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient

from flashcards.ai.pipeline.core import _grounded_page, _words
from flashcards.models import Card
from flashcards.tests.fakes import FACT_RE, FakeOpenAI, fact_line, fact_pages, make_pdf, paraphrase_of

ANALYZE = "/api/flashcards/analyze/"
GENERATE = "/api/flashcards/generate/"

# 6 pages, deliberately uneven TOC: Cells 1–4, Photosynthesis 5, Respiration 6.
# An even split would give 1–2 / 3–4 / 5–6 instead.
UNEVEN_TOC = [[1, "Cells", 1], [1, "Photosynthesis", 5], [1, "Respiration", 6]]
UNEVEN_RANGES = {"Cells": (1, 4), "Photosynthesis": (5, 5), "Respiration": (6, 6)}


def excerpt_page(card) -> int:
    return int(FACT_RE.search(card.excerpt).group(1))


class PipelineTestCase(TestCase):
    """Patches every model seam with a FakeOpenAI and blocks real HTTP."""

    fake_kwargs: dict = {}

    def setUp(self):
        self.fake = FakeOpenAI(**self.fake_kwargs)
        patches = [
            mock.patch("flashcards.ai.pipeline.templater.OpenAI", lambda *a, **k: self.fake),
            mock.patch("flashcards.ai.flashcard_gen.CLIENT", self.fake),
            mock.patch("httpx.Client.send", side_effect=AssertionError("network access in tests")),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        cache.clear()  # throttle and generation-budget counters

        # Route the views' temp files into a private dir so leaks are observable.
        self.tmpdir = tempfile.mkdtemp(prefix="flash-f05-")
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        old = tempfile.tempdir
        tempfile.tempdir = self.tmpdir
        self.addCleanup(setattr, tempfile, "tempdir", old)

        self.client = APIClient()

    def upload(self, data: bytes, name: str = "biology.pdf"):
        return SimpleUploadedFile(name, data, content_type="application/pdf")

    def analyze(self, data: bytes, name: str = "biology.pdf"):
        return self.client.post(ANALYZE, {"file": self.upload(data, name)}, format="multipart")

    def generate(self, data: bytes, name: str = "biology.pdf", **fields):
        payload = {"file": self.upload(data, name), "deck_name": name.rsplit(".", 1)[0]}
        payload.update({k: str(v) for k, v in fields.items()})
        return self.client.post(GENERATE, payload, format="multipart")

    def allocations(self, counts: dict[str, int], ranges=UNEVEN_RANGES) -> str:
        import json
        return json.dumps([
            {"title": t, "page_start": ranges[t][0], "page_end": ranges[t][1], "cards": n}
            for t, n in counts.items()
        ])


class AnalysisTests(PipelineTestCase):
    def test_analysis_preserves_real_toc_ranges(self):
        res = self.analyze(make_pdf(6, toc=UNEVEN_TOC))
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["pages"], 6)
        got = {s["title"]: (s["page_start"], s["page_end"]) for s in body["toc_sections"]}
        self.assertEqual(got, UNEVEN_RANGES)
        alloc = {a["title"]: (a["page_start"], a["page_end"]) for a in body["per_section_allocation"]}
        self.assertEqual(alloc, UNEVEN_RANGES)

    def test_recommendation_and_allocation_limits(self):
        body = self.analyze(make_pdf(6, toc=UNEVEN_TOC)).json()
        self.assertEqual(body["recommended_cards"], 3)  # one per section, floor 3
        self.assertEqual(body["suggested_range"], {"lo": 3, "hi": 4})
        self.assertEqual(sum(a["cards"] for a in body["per_section_allocation"]), 3)

        no_toc = self.analyze(make_pdf(5)).json()
        self.assertEqual(no_toc["toc_sections"], [])
        self.assertEqual(no_toc["per_section_allocation"], [])
        self.assertTrue(3 <= no_toc["recommended_cards"] <= 30)


class SectionSourcingTests(PipelineTestCase):
    def test_template_keeps_real_toc_ranges(self):
        res = self.generate(make_pdf(6, toc=UNEVEN_TOC),
                            allocations=self.allocations({"Cells": 4, "Photosynthesis": 2, "Respiration": 2}))
        self.assertEqual(res.status_code, 201, res.content)
        tpl = res.json()["template"]
        got = {s["title"]: (s["page_start"], s["page_end"]) for s in tpl["sections"]}
        self.assertEqual(got, UNEVEN_RANGES)
        self.assertEqual({s["page_source"] for s in tpl["sections"]}, {"toc"})
        self.assertEqual({t["title"]: (t["page_start"], t["page_end"]) for t in tpl["toc"]}, UNEVEN_RANGES)
        self.assertEqual(tpl["title"], "biology")  # deck name, not the server temp-file stem

    def test_section_inputs_do_not_cross_into_neighbours(self):
        self.generate(make_pdf(6, toc=UNEVEN_TOC),
                      allocations=self.allocations({"Cells": 4, "Photosynthesis": 2, "Respiration": 2}))
        for title, user in self.fake.template_inputs_by_hint().items():
            lo, hi = UNEVEN_RANGES[title]
            self.assertTrue(fact_pages(user), title)
            self.assertTrue(fact_pages(user) <= set(range(lo, hi + 1)), (title, fact_pages(user)))
        inputs = self.fake.card_inputs_by_section()
        self.assertEqual(set(inputs), set(UNEVEN_RANGES))
        for title, users in inputs.items():
            lo, hi = UNEVEN_RANGES[title]
            for user in users:
                pages = fact_pages(user)
                self.assertTrue(pages, f"{title} got no page text")
                self.assertTrue(pages <= set(range(lo, hi + 1)), (title, pages))

    def test_card_pages_are_grounded_in_source_pages(self):
        res = self.generate(make_pdf(6, toc=UNEVEN_TOC),
                            allocations=self.allocations({"Cells": 6, "Photosynthesis": 2, "Respiration": 2}))
        deck_id = res.json()["deck_id"]
        cards = list(Card.objects.filter(deck__public_id=deck_id))
        self.assertEqual(len(cards), 10)
        for c in cards:
            lo, hi = UNEVEN_RANGES[c.section]
            self.assertTrue(lo <= c.page <= hi, (c.section, c.page))
            self.assertEqual(c.page, excerpt_page(c), c.excerpt)
        # Cells spans 1–4; its 6 cards come from pages 1 and 2, not all "page 1".
        self.assertEqual({c.page for c in cards if c.section == "Cells"}, {1, 2})

    def test_nested_toc_entries_on_same_page_stay_separate(self):
        # "Unit" (1–1) and its child "Intro" (1–2) start on the same page.
        toc = [[1, "Unit", 1], [2, "Intro", 1], [1, "Next", 3]]
        ranges = {"Unit": (1, 1), "Intro": (1, 2), "Next": (3, 4)}
        res = self.generate(make_pdf(4, toc=toc),
                            allocations=self.allocations({"Unit": 1, "Intro": 2, "Next": 1}, ranges))
        self.assertEqual(res.status_code, 201, res.content)
        inputs = self.fake.card_inputs_by_section()
        inputs.pop("Mixed topics", None)  # catch-up after cross-section dedup
        self.assertEqual(set(inputs), set(ranges))
        for title, users in inputs.items():
            lo, hi = ranges[title]
            for user in users:
                self.assertTrue(fact_pages(user) <= set(range(lo, hi + 1)), (title, fact_pages(user)))

    def test_no_toc_sections_use_their_own_pages(self):
        res = self.generate(make_pdf(3), cards_wanted=6)
        self.assertEqual(res.status_code, 201, res.content)
        tpl = res.json()["template"]
        self.assertEqual([(s["page_start"], s["page_end"], s["page_source"]) for s in tpl["sections"]],
                         [(1, 1, "page"), (2, 2, "page"), (3, 3, "page")])
        for c in Card.objects.filter(deck__public_id=res.json()["deck_id"]):
            self.assertEqual(c.page, excerpt_page(c))

    def test_estimated_ranges_only_without_page_metadata(self):
        # If structural extraction fails, ranges are approximate: they are
        # flagged, and no page text or unverified page number is used.
        with mock.patch("flashcards.ai.pipeline.templater.run_extraction", side_effect=RuntimeError("boom")):
            res = self.generate(make_pdf(4), cards_wanted=3)
        self.assertEqual(res.status_code, 201, res.content)
        sections = res.json()["template"]["sections"]
        self.assertTrue(sections)
        self.assertEqual({s["page_source"] for s in sections}, {"estimated"})
        for users in self.fake.card_inputs_by_section().values():
            for user in users:
                self.assertFalse(fact_pages(user))
                self.assertNotRegex(user, r"(?m)^PAGE: ")
        self.assertEqual({c.page for c in Card.objects.filter(deck__public_id=res.json()["deck_id"])}, {None})


class NoTocMergeTests(PipelineTestCase):
    fake_kwargs = {"notoc_titles": {1: "Alpha", 2: "Alpha", 3: "Gamma"}}

    def test_adjacent_pages_with_same_title_merge_into_one_range(self):
        res = self.generate(make_pdf(3), cards_wanted=4)
        tpl = res.json()["template"]
        self.assertEqual([(s["title"], s["page_start"], s["page_end"]) for s in tpl["sections"]],
                         [("Alpha", 1, 2), ("Gamma", 3, 3)])


class PersistenceTests(PipelineTestCase):
    def test_ordinals_are_dense_and_match_doc_order(self):
        res = self.generate(make_pdf(6, toc=UNEVEN_TOC),
                            allocations=self.allocations({"Cells": 4, "Photosynthesis": 2, "Respiration": 2}))
        deck_id = res.json()["deck_id"]
        hand = self.client.get("/api/flashcards/hand/", {"deck_id": deck_id, "n": "all", "order": "doc"}).json()
        toc = self.client.get("/api/flashcards/toc/", {"deck_id": deck_id}).json()
        self.assertEqual([c["ordinal"] for c in hand], list(range(1, len(hand) + 1)))
        self.assertEqual([(t["id"], t["ordinal"]) for t in toc], [(c["id"], c["ordinal"]) for c in hand])
        pages = [c["page"] for c in hand]
        self.assertEqual(pages, sorted(pages))

    def test_catch_up_cards_get_no_fake_page(self):
        # 30 wanted, but only 3+3 facts exist outside "Cells" → global catch-up runs.
        res = self.generate(make_pdf(6, toc=UNEVEN_TOC), cards_wanted=30)
        self.assertEqual(res.status_code, 201, res.content)
        cards = list(Card.objects.filter(deck__public_id=res.json()["deck_id"]))
        mixed = [c for c in cards if c.section == "Mixed topics"]
        self.assertTrue(mixed)
        self.assertEqual({c.page for c in mixed}, {None})
        hand = self.client.get("/api/flashcards/hand/",
                               {"deck_id": res.json()["deck_id"], "n": "all", "order": "doc"}).json()
        self.assertEqual([c["ordinal"] for c in hand], list(range(1, len(hand) + 1)))
        self.assertEqual(hand[-1]["section"], "Mixed topics")  # unknown pages sort last


class RequestLimitTests(PipelineTestCase):
    """Count/allocation limits the frontend relies on (3–30 total, ≤8 per section)."""

    def test_cards_wanted_is_clamped(self):
        pdf = make_pdf(6, toc=UNEVEN_TOC)
        for sent, expected in (("1", 3), ("99", 30), ("abc", 12)):
            res = self.generate(pdf, cards_wanted=sent)
            self.assertEqual(res.status_code, 201, res.content)
            self.assertEqual(res.json()["requested"], expected, sent)
            self.assertLessEqual(res.json()["cards_created"], expected)

    def test_per_section_allocation_capped_at_eight(self):
        res = self.generate(make_pdf(6, toc=UNEVEN_TOC),
                            allocations=self.allocations({"Cells": 20, "Photosynthesis": 1, "Respiration": 1}))
        body = res.json()
        self.assertEqual(body["requested"], 10)
        per = {p["title"]: p for p in body["per_section"]}
        self.assertEqual(per["Cells"]["planned"], 8)
        self.assertEqual(per["Cells"]["created"], 8)
        # F5 added `partial`; every F0.5 key is unchanged.
        self.assertEqual(set(body), {"deck_id", "template", "cards_created", "requested", "partial",
                                     "warnings", "per_section"})


class TempAndErrorHygieneTests(PipelineTestCase):
    def test_generate_leaves_no_temp_files(self):
        res = self.generate(make_pdf(6, toc=UNEVEN_TOC), cards_wanted=3)
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(list(pathlib.Path(self.tmpdir).iterdir()), [])

    def test_parse_errors_do_not_expose_server_paths(self):
        # F5: a corrupt PDF is the client's input, so it is a 400 with a fixed
        # message (F0.5 answered 500 with the sanitised exception text).
        for url in (ANALYZE, GENERATE):
            res = self.client.post(url, {"file": self.upload(b"%PDF-1.4 not really", "broken.pdf")},
                                   format="multipart")
            self.assertEqual(res.status_code, 400)
            self.assertEqual(res.json()["code"], "pdf_unreadable")
            detail = res.json()["detail"]
            self.assertNotIn(self.tmpdir, detail)
            self.assertNotIn(self.tmpdir.replace("\\", "/"), detail)
            self.assertNotIn("tmp", detail.replace(self.tmpdir, ""))
        self.assertEqual(list(pathlib.Path(self.tmpdir).iterdir()), [])


class PageGroundingPolicyTests(SimpleTestCase):
    """_grounded_page: a page is claimed only when the pipeline can ground it."""

    # Normalised text of a 6-page synthetic document (3 facts per page).
    PAGES = [" " + " ".join(_words(" ".join(fact_line(p, n) for n in (1, 2, 3)))) + " "
             for p in range(1, 7)]

    @staticmethod
    def sec(ps, pe, source="toc"):
        return {"title": "S", "page_start": ps, "page_end": pe, "page_source": source}

    def test_a_exact_excerpt_in_multi_page_section_returns_its_page(self):
        card = {"excerpt": fact_line(3, 2)}
        self.assertEqual(_grounded_page(card, self.sec(1, 4), self.PAGES), 3)

    def test_b_unmatched_excerpt_in_multi_page_section_is_unknown(self):
        card = {"excerpt": paraphrase_of(2)}
        self.assertIsNone(_grounded_page(card, self.sec(1, 4), self.PAGES))

    def test_b_model_echoed_page_is_not_provenance(self):
        card = {"excerpt": paraphrase_of(2), "page": 2}
        self.assertIsNone(_grounded_page(card, self.sec(1, 4), self.PAGES))

    def test_b_excerpt_outside_the_section_range_is_not_used(self):
        card = {"excerpt": fact_line(6, 1)}  # real text, but from another section's page
        self.assertIsNone(_grounded_page(card, self.sec(1, 4), self.PAGES))

    def test_c_unmatched_excerpt_in_single_page_section_returns_that_page(self):
        card = {"excerpt": paraphrase_of(1)}
        self.assertEqual(_grounded_page(card, self.sec(5, 5), self.PAGES), 5)

    def test_d_no_real_range_is_unknown_unless_excerpt_is_found(self):
        unmatched = {"excerpt": paraphrase_of(1)}
        self.assertIsNone(_grounded_page(unmatched, self.sec(1, 3, "estimated"), self.PAGES))
        self.assertIsNone(_grounded_page(unmatched, None, self.PAGES))
        # Rule 4: a verbatim excerpt is independent grounding, wherever it is.
        found = {"excerpt": fact_line(4, 3)}
        self.assertEqual(_grounded_page(found, self.sec(1, 3, "estimated"), self.PAGES), 4)
        self.assertEqual(_grounded_page(found, None, self.PAGES), 4)


class ParaphrasedExcerptTests(PipelineTestCase):
    fake_kwargs = {"paraphrase": True}

    def test_unmatched_excerpts_get_no_fake_page(self):
        res = self.generate(make_pdf(6, toc=UNEVEN_TOC),
                            allocations=self.allocations({"Cells": 4, "Photosynthesis": 2, "Respiration": 2}))
        self.assertEqual(res.status_code, 201, res.content)
        deck_id = res.json()["deck_id"]
        pages = {(c.section, c.page) for c in Card.objects.filter(deck__public_id=deck_id)}
        # Cells spans 1–4 → unknown (not page 1); single-page sections keep their page.
        self.assertEqual(pages, {("Cells", None), ("Photosynthesis", 5), ("Respiration", 6)})
        hand = self.client.get("/api/flashcards/hand/", {"deck_id": deck_id, "n": "all", "order": "doc"}).json()
        self.assertEqual([c["ordinal"] for c in hand], list(range(1, 9)))
        self.assertEqual([c["section"] for c in hand][-4:], ["Cells"] * 4)  # unknown pages sort last
