"""
F5: production settings must come from the environment and fail closed.

Each case imports flashsite.settings in a fresh interpreter with a fully
specified environment (every variable set, "" meaning unset), so neither
the developer's shell nor a local .env file can influence the result.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

BASE_DIR = Path(settings.BASE_DIR)
GOOD_KEY = "prod-like-placeholder-" + "k9Qz7Xw2" * 6  # 70 chars, not a real secret

VARS = [
    "DJANGO_DEBUG", "DJANGO_SECRET_KEY", "DJANGO_ALLOWED_HOSTS", "DJANGO_CORS_ALLOWED_ORIGINS",
    "DJANGO_NUM_PROXIES", "DJANGO_SECURE_SSL_REDIRECT", "DJANGO_SECURE_HSTS_SECONDS",
    "DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", "DJANGO_SECURE_HSTS_PRELOAD", "DJANGO_SECURE_PROXY_SSL_HEADER",
    "DJANGO_LOG_LEVEL", "OPENAI_API_KEY", "OPENAI_TIMEOUT_SECONDS", "FLASH_CACHE", "FLASH_MAX_UPLOAD_MB",
    "FLASH_MAX_PDF_PAGES", "FLASH_GENERATION_DAILY_LIMIT", "FLASH_THROTTLE_ANALYZE", "FLASH_THROTTLE_GENERATE",
    "FLASH_THROTTLE_READ",
]
PROD = {"DJANGO_SECRET_KEY": GOOD_KEY, "DJANGO_ALLOWED_HOSTS": "api.example.com",
        "DJANGO_CORS_ALLOWED_ORIGINS": "https://app.example.com"}

PROBE = """
import json
import flashsite.settings as s
print(json.dumps({
    "DEBUG": s.DEBUG, "SECRET_KEY": s.SECRET_KEY, "ALLOWED_HOSTS": s.ALLOWED_HOSTS,
    "CORS_ALLOWED_ORIGINS": s.CORS_ALLOWED_ORIGINS, "CORS_ALLOWED_ORIGIN_REGEXES": s.CORS_ALLOWED_ORIGIN_REGEXES,
    "CORS_ALLOW_ALL_ORIGINS": s.CORS_ALLOW_ALL_ORIGINS, "CORS_ALLOW_CREDENTIALS": s.CORS_ALLOW_CREDENTIALS,
    "SESSION_COOKIE_SECURE": s.SESSION_COOKIE_SECURE, "CSRF_COOKIE_SECURE": s.CSRF_COOKIE_SECURE,
    "OPENAI_API_KEY": s.OPENAI_API_KEY, "FLASH_MAX_UPLOAD_MB": s.FLASH_MAX_UPLOAD_MB,
    "FLASH_THROTTLE_RATES": s.FLASH_THROTTLE_RATES, "FLASH_GENERATION_DAILY_LIMIT": s.FLASH_GENERATION_DAILY_LIMIT,
    "AUTH": s.REST_FRAMEWORK["DEFAULT_AUTHENTICATION_CLASSES"], "LOG_LEVEL": s.LOGGING["root"]["level"],
}))
"""


def run(args: list[str], env_overrides: dict[str, str]) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in VARS and k != "DJANGO_SETTINGS_MODULE"}
    env.update({k: "" for k in VARS})
    env.update(env_overrides)
    env["PYTHONPATH"] = str(BASE_DIR)
    return subprocess.run([sys.executable, *args], cwd=BASE_DIR, env=env, capture_output=True, text=True, timeout=120)


def load(**env) -> dict:
    proc = run(["-c", PROBE], env)
    if proc.returncode != 0:
        raise AssertionError(proc.stderr.strip().splitlines()[-1])
    return json.loads(proc.stdout)


class SettingsSafetyTests(SimpleTestCase):
    def assertRefuses(self, message: str, **env):
        with self.assertRaises(AssertionError) as ctx:
            load(**env)
        self.assertIn("ImproperlyConfigured", str(ctx.exception))
        self.assertIn(message, str(ctx.exception))

    def test_debug_is_off_by_default_and_a_secret_key_is_required(self):
        self.assertRefuses("DJANGO_SECRET_KEY is required")

    def test_hardcoded_or_weak_keys_are_refused_in_production(self):
        for weak in ("django-insecure-" + "x1y2z3" * 10, "short-key", "a" * 60):
            with self.subTest(key=weak[:20]):
                self.assertRefuses("too weak", DJANGO_SECRET_KEY=weak, DJANGO_ALLOWED_HOSTS="api.example.com")
        source = (BASE_DIR / "flashsite" / "settings.py").read_text(encoding="utf-8")
        self.assertNotIn("sx@&*^r4^6", source)  # the key committed before F5

    def test_production_needs_explicit_hosts(self):
        self.assertRefuses("DJANGO_ALLOWED_HOSTS is required", DJANGO_SECRET_KEY=GOOD_KEY)
        self.assertRefuses("explicit host names", DJANGO_SECRET_KEY=GOOD_KEY, DJANGO_ALLOWED_HOSTS="*")

    def test_cors_wildcard_is_refused(self):
        self.assertRefuses("explicit origins", **{**PROD, "DJANGO_CORS_ALLOWED_ORIGINS": "*"})
        self.assertRefuses("explicit origins", DJANGO_DEBUG="true", DJANGO_CORS_ALLOWED_ORIGINS="*")

    def test_invalid_values_are_refused(self):
        self.assertRefuses("DJANGO_DEBUG must be true or false", DJANGO_DEBUG="maybe")
        self.assertRefuses("must be a whole number", **PROD, FLASH_MAX_UPLOAD_MB="20MB")
        self.assertRefuses("FLASH_CACHE must be", **PROD, FLASH_CACHE="redis")

    def test_production_configuration(self):
        s = load(**PROD)
        self.assertFalse(s["DEBUG"])
        self.assertEqual(s["SECRET_KEY"], GOOD_KEY)
        self.assertEqual(s["ALLOWED_HOSTS"], ["api.example.com"])
        self.assertEqual(s["CORS_ALLOWED_ORIGINS"], ["https://app.example.com"])
        self.assertEqual(s["CORS_ALLOWED_ORIGIN_REGEXES"], [])
        self.assertFalse(s["CORS_ALLOW_ALL_ORIGINS"])
        self.assertFalse(s["CORS_ALLOW_CREDENTIALS"])
        self.assertTrue(s["SESSION_COOKIE_SECURE"] and s["CSRF_COOKIE_SECURE"])
        self.assertEqual(s["AUTH"], [])
        self.assertEqual(s["OPENAI_API_KEY"], "")  # optional: generation answers 503 without it
        self.assertEqual(s["FLASH_MAX_UPLOAD_MB"], 20)
        self.assertEqual(s["FLASH_THROTTLE_RATES"], {"analyze": "30/hour", "generate": "10/hour", "read": "120/minute"})
        self.assertEqual(s["FLASH_GENERATION_DAILY_LIMIT"], 100)
        self.assertEqual(s["LOG_LEVEL"], "INFO")

    def test_local_development_needs_only_debug(self):
        s = load(DJANGO_DEBUG="true")
        self.assertTrue(s["DEBUG"])
        self.assertTrue(s["SECRET_KEY"].startswith("django-insecure-local-development-only"))
        self.assertIn("localhost", s["ALLOWED_HOSTS"])
        self.assertIn("127.0.0.1", s["ALLOWED_HOSTS"])
        self.assertNotIn("*", s["ALLOWED_HOSTS"])
        # Any localhost port (Expo web, a static export) but nothing else.
        import re
        (pattern,) = s["CORS_ALLOWED_ORIGIN_REGEXES"]
        for ok in ("http://localhost:8081", "http://127.0.0.1:19006", "http://localhost"):
            self.assertRegex(ok, pattern)
        for bad in ("http://evil.example", "http://localhost.evil.example", "https://127.0.0.1.nip.io"):
            self.assertIsNone(re.match(pattern, bad), bad)

    def test_tunable_limits(self):
        s = load(**PROD, FLASH_MAX_UPLOAD_MB="5", FLASH_THROTTLE_GENERATE="3/day", FLASH_GENERATION_DAILY_LIMIT="0")
        self.assertEqual(s["FLASH_MAX_UPLOAD_MB"], 5)
        self.assertEqual(s["FLASH_THROTTLE_RATES"]["generate"], "3/day")
        self.assertEqual(s["FLASH_GENERATION_DAILY_LIMIT"], 0)


class DeployCheckTests(SimpleTestCase):
    """`manage.py check --deploy` with production-style placeholder values."""

    def warnings(self, **env) -> list[str]:
        import re
        proc = run(["manage.py", "check", "--deploy"], env)
        out = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, out)
        return sorted(set(re.findall(r"\(security\.(W\d+)\)", out)))

    def test_full_production_profile_is_clean(self):
        self.assertEqual(self.warnings(
            **PROD, DJANGO_SECURE_SSL_REDIRECT="true", DJANGO_SECURE_HSTS_SECONDS="31536000",
            DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS="true", DJANGO_SECURE_HSTS_PRELOAD="true"), [])

    def test_minimal_profile_leaves_only_the_hosting_layer_warnings(self):
        # HSTS and the HTTPS redirect are opt-in because TLS is usually
        # terminated by the host or proxy (see README, Deployment caveats).
        self.assertEqual(self.warnings(**PROD), ["W004", "W008"])

    def test_hsts_without_domain_wide_commitments(self):
        # includeSubDomains / preload bind every subdomain of the domain:
        # the domain owner's decision, not this app's.
        self.assertEqual(self.warnings(**PROD, DJANGO_SECURE_SSL_REDIRECT="true",
                                       DJANGO_SECURE_HSTS_SECONDS="31536000"), ["W005", "W021"])
