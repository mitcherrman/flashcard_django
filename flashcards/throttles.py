"""
Abuse and cost controls for an anonymous demo (no accounts, no Redis).

* Per-client-IP rates with DRF's throttling, one scope per kind of work:
  `generate` (model calls cost money) is the strictest, `analyze` (local
  PDF parsing) is looser, `read` (hand/toc) is generous.
* A server-wide daily generation budget (`consume_generation_budget`), so
  rotating IP addresses cannot run up an unbounded model bill.

Counters live in Django's default cache (settings.CACHES): per process with
the default "locmem" backend, shared across workers with FLASH_CACHE=database.
Rates are read from settings on every request, so they can be tuned (and
overridden in tests) without code changes.
"""
from __future__ import annotations

import datetime as dt

from django.conf import settings
from django.core.cache import cache
from rest_framework.throttling import SimpleRateThrottle

from .errors import GenerationLimitReached


class _ScopedIpThrottle(SimpleRateThrottle):
    scope = ""

    def get_rate(self):
        return (getattr(settings, "FLASH_THROTTLE_RATES", {}) or {}).get(self.scope)

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


class AnalyzeThrottle(_ScopedIpThrottle):
    scope = "analyze"


class GenerateThrottle(_ScopedIpThrottle):
    scope = "generate"


class ReadThrottle(_ScopedIpThrottle):
    scope = "read"


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def consume_generation_budget() -> None:
    """
    Count one generation against today's server-wide budget, or raise 429
    `generation_limit_reached` until the next UTC midnight. Called only after
    the request has been validated, so rejected uploads don't spend it.
    """
    limit = getattr(settings, "FLASH_GENERATION_DAILY_LIMIT", None)
    if limit is None:
        return
    now = _utc_now()
    key = f"flash:generations:{now.date().isoformat()}"
    cache.add(key, 0, timeout=2 * 24 * 3600)
    try:
        used = cache.incr(key)
    except ValueError:  # evicted between add and incr
        cache.set(key, 1, timeout=2 * 24 * 3600)
        used = 1
    if used > int(limit):
        midnight = dt.datetime.combine(now.date() + dt.timedelta(days=1), dt.time(), tzinfo=dt.timezone.utc)
        raise GenerationLimitReached(wait=(midnight - now).total_seconds())
