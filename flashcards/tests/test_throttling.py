"""
F5: cost and abuse controls. Per-IP rates by kind of work (generate is the
strictest, analyze looser, reads generous) and a server-wide daily budget
for model-backed generation. Counters use Django's cache (cleared per test).
"""
from __future__ import annotations

import datetime as dt
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from flashcards.models import Deck
from flashcards.tests.fakes import make_pdf
from flashcards.tests.test_pipeline import ANALYZE, GENERATE, UNEVEN_TOC, PipelineTestCase

RATES = {"analyze": "4/hour", "generate": "2/hour", "read": "3/minute"}
PDF = make_pdf(6, toc=UNEVEN_TOC)


@override_settings(FLASH_THROTTLE_RATES=RATES)
class PerClientThrottleTests(PipelineTestCase):
    def gen(self, ip="10.0.0.1"):
        return self.client.post(GENERATE, {"file": self.upload(PDF), "deck_name": "bio", "cards_wanted": "3"},
                                format="multipart", REMOTE_ADDR=ip)

    def ana(self, ip="10.0.0.1"):
        return self.client.post(ANALYZE, {"file": self.upload(PDF)}, format="multipart", REMOTE_ADDR=ip)

    def assertThrottled(self, res, code="throttled"):
        self.assertEqual(res.status_code, 429, res.content)
        body = res.json()
        self.assertEqual(body["code"], code)
        self.assertRegex(body["detail"], r"Try again (in about \d+ (minute|minutes|hours)|later)\.$")
        self.assertNotIn("Expected available", body["detail"])
        self.assertGreater(int(res["Retry-After"]), 0)
        self.assertEqual(body["retry_after"], int(res["Retry-After"]))

    def test_generate_is_limited_per_client(self):
        self.assertEqual([self.gen().status_code for _ in range(2)], [201, 201])
        calls = len(self.fake.calls)
        self.assertThrottled(self.gen())
        self.assertEqual(len(self.fake.calls), calls, "a throttled request reached the model")
        self.assertEqual(Deck.objects.count(), 2)
        # Another client is unaffected.
        self.assertEqual(self.gen(ip="10.0.0.2").status_code, 201)

    def test_analyze_is_less_restrictive_and_counted_separately(self):
        self.gen(), self.gen()
        self.assertThrottled(self.gen())
        self.assertEqual([self.ana().status_code for _ in range(4)], [200] * 4)
        self.assertThrottled(self.ana())

    def test_reads_are_limited(self):
        deck_id = self.gen().json()["deck_id"]
        codes = [self.client.get("/api/flashcards/toc/", {"deck_id": deck_id}, REMOTE_ADDR="10.0.0.9").status_code
                 for _ in range(4)]
        self.assertEqual(codes, [200, 200, 200, 429])

    def test_invalid_requests_count_too(self):
        # Probing with junk uploads spends the same per-client allowance.
        bad = lambda: self.client.post(GENERATE, {"file": SimpleUploadedFile("x.pdf", b"nope")},
                                       format="multipart", REMOTE_ADDR="10.0.0.1")
        self.assertEqual([bad().status_code for _ in range(2)], [400, 400])
        self.assertThrottled(bad())

    def test_health_is_never_throttled(self):
        codes = {self.client.get("/api/flashcards/health/").status_code for _ in range(20)}
        self.assertEqual(codes, {200})

    @override_settings(REST_FRAMEWORK={"NUM_PROXIES": 1, "EXCEPTION_HANDLER": "flashcards.errors.api_exception_handler",
                                       "DEFAULT_AUTHENTICATION_CLASSES": []})
    def test_behind_one_proxy_the_forwarded_client_ip_is_used(self):
        via_proxy = lambda client_ip: self.client.post(
            GENERATE, {"file": self.upload(PDF), "deck_name": "bio", "cards_wanted": "3"}, format="multipart",
            REMOTE_ADDR="192.0.2.10", HTTP_X_FORWARDED_FOR=client_ip)
        self.assertEqual([via_proxy("203.0.113.5").status_code for _ in range(2)], [201, 201])
        self.assertEqual(via_proxy("203.0.113.5").status_code, 429)
        self.assertEqual(via_proxy("203.0.113.6").status_code, 201)  # same proxy, different client


class GenerationBudgetTests(PipelineTestCase):
    def gen(self, ip):
        return self.client.post(GENERATE, {"file": self.upload(PDF), "deck_name": "bio", "cards_wanted": "3"},
                                format="multipart", REMOTE_ADDR=ip)

    @override_settings(FLASH_GENERATION_DAILY_LIMIT=2)
    def test_daily_budget_is_shared_by_every_client(self):
        self.assertEqual([self.gen(f"10.1.0.{i}").status_code for i in (1, 2)], [201, 201])
        calls = len(self.fake.calls)
        res = self.gen("10.1.0.3")
        self.assertEqual(res.status_code, 429)
        self.assertEqual(res.json()["code"], "generation_limit_reached")
        self.assertIn("daily limit", res.json()["detail"])
        self.assertLessEqual(int(res["Retry-After"]), 24 * 3600)
        self.assertEqual(len(self.fake.calls), calls)
        self.assertEqual(Deck.objects.count(), 2)

    @override_settings(FLASH_GENERATION_DAILY_LIMIT=1)
    def test_rejected_requests_do_not_spend_the_budget(self):
        junk = self.client.post(GENERATE, {"file": SimpleUploadedFile("x.pdf", b"nope")}, format="multipart")
        bad_plan = self.client.post(GENERATE, {"file": self.upload(PDF), "allocations": "{not json"},
                                    format="multipart")
        self.assertEqual((junk.status_code, bad_plan.status_code), (400, 400))
        self.assertEqual(self.gen("10.2.0.1").status_code, 201)
        self.assertEqual(self.gen("10.2.0.2").status_code, 429)

    @override_settings(FLASH_GENERATION_DAILY_LIMIT=0)
    def test_zero_turns_generation_off(self):
        res = self.gen("10.3.0.1")
        self.assertEqual(res.status_code, 429)
        self.assertEqual(res.json()["code"], "generation_limit_reached")
        self.assertEqual(self.fake.calls, [])
        # Analysis needs no model and keeps working.
        self.assertEqual(self.client.post(ANALYZE, {"file": self.upload(PDF)}, format="multipart").status_code, 200)

    @override_settings(FLASH_GENERATION_DAILY_LIMIT=1)
    def test_budget_resets_at_utc_midnight(self):
        day1 = dt.datetime(2026, 1, 1, 23, 59, tzinfo=dt.timezone.utc)
        with mock.patch("flashcards.throttles._utc_now", return_value=day1):
            self.assertEqual(self.gen("10.4.0.1").status_code, 201)
            res = self.gen("10.4.0.2")
            self.assertEqual(res.status_code, 429)
            self.assertEqual(int(res["Retry-After"]), 60)
        with mock.patch("flashcards.throttles._utc_now", return_value=day1 + dt.timedelta(minutes=2)):
            self.assertEqual(self.gen("10.4.0.3").status_code, 201)
