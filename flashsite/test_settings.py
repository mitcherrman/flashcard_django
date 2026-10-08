"""
Settings for `python manage.py test` (manage.py selects this module for the
`test` command unless DJANGO_SETTINGS_MODULE is already set).

Deterministic placeholder configuration: production-style (DEBUG off), a
fixed non-secret key, and a fake model key that is never used — every model
seam is patched with FakeOpenAI and real HTTP is blocked by the tests.
These values are forced, so a developer's shell or `.env` cannot change
what the suite exercises.
"""
import os

os.environ.update({
    "DJANGO_DEBUG": "false",
    "DJANGO_SECRET_KEY": "test-only-secret-key-0123456789-abcdefghijklmnopqrstuvwxyz-not-for-production",
    "DJANGO_ALLOWED_HOSTS": "testserver,localhost",
    "DJANGO_CORS_ALLOWED_ORIGINS": "http://localhost:8081",
    "OPENAI_API_KEY": "unused-test-key",
    "FLASH_CACHE": "locmem",
    "FLASH_MAX_UPLOAD_MB": "20",
    "FLASH_MAX_PDF_PAGES": "200",
    "FLASH_GENERATION_DAILY_LIMIT": "1000",
    # Generous rates so functional tests never trip them; throttling tests
    # override FLASH_THROTTLE_RATES explicitly.
    "FLASH_THROTTLE_ANALYZE": "1000/minute",
    "FLASH_THROTTLE_GENERATE": "1000/minute",
    "FLASH_THROTTLE_READ": "1000/minute",
    "DJANGO_LOG_LEVEL": "CRITICAL",
})

from .settings import *  # noqa: E402,F401,F403

# Expected 4xx/5xx in tests would otherwise print "Bad Request: …" lines.
LOGGING["handlers"]["console"]["level"] = "CRITICAL"  # noqa: F405
