# Flashcard Maker — backend

Django + Django REST Framework API behind the Flashcard Maker app
([`mitcherrman/flash_ui`](https://github.com/mitcherrman/flash_ui)). It analyses an
uploaded PDF (no model calls), generates a study template and source-grounded
flashcards with an OpenAI model, stores the deck, and serves it for study.

There is **no verified production deployment**. The configuration below is
hardened for a small public demo; it is not an enterprise security baseline.

## Quick start (local)

```bash
python -m venv .venv
.venv/Scripts/activate            # Windows; on macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env              # sets DJANGO_DEBUG=true for local development
python manage.py migrate
python manage.py runserver        # http://127.0.0.1:8000
```

`GET /api/flashcards/health/` returns `{"ok": true}`.

Generation needs `OPENAI_API_KEY`. Without it, `/generate/` answers
`503 generation_unavailable` and everything else works. To exercise
generation without OpenAI, run any OpenAI-compatible stub locally and set
`OPENAI_BASE_URL` (read by the OpenAI SDK) plus a placeholder
`OPENAI_API_KEY`.

For a phone on the LAN (Expo Go), run `python manage.py runserver 0.0.0.0:8000`
and add the machine's LAN IP to `DJANGO_ALLOWED_HOSTS` (with `DJANGO_DEBUG=true`
the localhost names are already allowed).

## Tests

```bash
python manage.py test flashcards
```

No environment or `.env` is needed: `manage.py` selects
`flashsite/test_settings.py` for the `test` command, which forces deterministic,
production-style placeholder values (DEBUG off). The suite never calls OpenAI
(every model seam is patched with `flashcards/tests/fakes.FakeOpenAI` and real
HTTP is blocked), uses synthetic PDFs built in memory, and runs on Django's
test SQLite database. Other checks:

```bash
DJANGO_DEBUG=true python manage.py check
DJANGO_DEBUG=true python manage.py makemigrations --check --dry-run
```

## Configuration

All settings come from the environment, or a gitignored `.env` file
(python-decouple). An empty value counts as unset. See `.env.example`.

| Variable | Default | Purpose |
|---|---|---|
| `DJANGO_DEBUG` | `false` | Never `true` on a public server. |
| `DJANGO_SECRET_KEY` | — | **Required when DEBUG is off**: 50+ random characters, not `django-insecure-…`. With DEBUG on, a fixed dev-only key is used. |
| `DJANGO_ALLOWED_HOSTS` | — | **Required when DEBUG is off**: comma-separated host names; `*` is refused. With DEBUG on: `localhost`, `*.localhost`, `127.0.0.1`, `[::1]`. |
| `DJANGO_CORS_ALLOWED_ORIGINS` | — | Comma-separated browser origins of the frontend, e.g. `https://flashcards.example.com`. `*` is refused. With DEBUG on and unset, any `http://localhost`/`127.0.0.1` port is allowed. |
| `OPENAI_API_KEY` | — | Model key. Optional (see above). |
| `OPENAI_BASE_URL` | OpenAI | Read by the OpenAI SDK; for a local stub. |
| `OPENAI_TIMEOUT_SECONDS` | `60` | Per model call. |
| `FLASH_MAX_UPLOAD_MB` | `20` | Larger uploads get `413`. |
| `FLASH_MAX_PDF_PAGES` | `200` | Longer PDFs get `400 too_many_pages`. |
| `FLASH_THROTTLE_ANALYZE` | `30/hour` | Per client IP. |
| `FLASH_THROTTLE_GENERATE` | `10/hour` | Per client IP (model calls cost money). |
| `FLASH_THROTTLE_READ` | `120/minute` | Per client IP, for `hand` / `toc`. |
| `FLASH_GENERATION_DAILY_LIMIT` | `100` | New decks per UTC day across **all** clients; `0` turns generation off. |
| `FLASH_CACHE` | `locmem` | Where throttle counters live: `locmem` or `database` (run `python manage.py createcachetable` once). |
| `DJANGO_NUM_PROXIES` | unset | Reverse proxies in front of Django, so throttles read the client IP from `X-Forwarded-For`. |
| `DJANGO_SECURE_PROXY_SSL_HEADER` | `false` | Trust `X-Forwarded-Proto: https` from your proxy. |
| `DJANGO_SECURE_SSL_REDIRECT` | `false` | Redirect HTTP to HTTPS in Django. |
| `DJANGO_SECURE_HSTS_SECONDS` | `0` | HSTS max-age. |
| `DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS` / `_PRELOAD` | `false` | Domain-wide HSTS commitments. |
| `DJANGO_LOG_LEVEL` | `INFO` | `DEBUG` also logs model output excerpts. |

Rates use DRF's format: `<count>/<second|minute|hour|day>`.

The frontend's API origin is configured on the frontend side with
`EXPO_PUBLIC_API_BASE` (server origin only, e.g. `https://api.example.com`); it
is inlined into the public JS bundle, so it must never hold a secret.

## API

All paths are under `/api/flashcards/`. Every request is anonymous: there are no
accounts, no session cookies and no CSRF-relevant credentials.

| Endpoint | Method | Notes |
|---|---|---|
| `analyze/` (alias `inspect/`) | POST multipart `file` | Pages, words, TOC sections, recommended count, per-section plan. No model calls. |
| `generate/` | POST multipart `file`, `deck_name`, `cards_wanted` (3–30), `allocations` (JSON list of `{title, page_start, page_end, cards}`, ≤ 8 per section, ≤ 30 in total) | `201 {deck_id, template, cards_created, requested, partial, warnings, per_section}` |
| `hand/?deck_id=<id>&n=all&order=doc` | GET | The deck's cards in document order. |
| `toc/?deck_id=<id>` | GET | `[{id, ordinal, front, section, page, context}]`. |
| `health/` | GET | Liveness. |

**Deck ids** are opaque public ids (22 URL-safe characters, 128 random bits).
The integer primary key is internal and is not accepted by the API, so decks
can't be found by counting. Anyone holding a deck's id can read it (there are
no accounts): treat it like an unlisted link.

**Missing vs empty:** an unknown deck is `404 deck_not_found`; an existing deck
with no cards is `200 []`. `generate` never creates a deck without cards.

**Generation outcomes:**

- Every requested card made: `201`, `partial: false`.
- Fewer than requested: still `201`, with `partial: true` and a first warning
  `"Created N of M requested cards."`. `cards_created` is always the real count.
- A section that came up short gets a warning; cards added by the catch-up
  pass are filed under `"Mixed topics"` (listed in `per_section`) with a warning
  saying so.
- No cards at all, or the model unreachable: `502 generation_failed`, and
  nothing is saved.

### Errors

Every error is JSON `{"detail": "<user-safe message>", "code": "<code>"}`
(sometimes with an extra field). Exception text, file names and server paths
never appear in responses; unexpected failures are logged server-side with
their traceback and answered with a generic `500`.

| Status | Codes |
|---|---|
| 400 | `file_required`, `not_pdf`, `pdf_unreadable`, `pdf_encrypted`, `too_many_pages` (+`pages`, `limit_pages`), `no_text`, `invalid_allocations`, `deck_id_required`, `parse_error`, `bad_request` (e.g. a Host not in `DJANGO_ALLOWED_HOSTS`) |
| 404 | `deck_not_found`, `not_found` |
| 405 / 415 | `method_not_allowed` / `unsupported_media_type` |
| 413 | `file_too_large` (+`limit_mb`) |
| 429 | `throttled`, `generation_limit_reached` (+`retry_after`; `Retry-After` header) |
| 500 | `server_error` |
| 502 | `generation_failed` |
| 503 | `generation_unavailable` |

### Uploads

The server validates every upload itself: size (checked while the body is
received, so oversized files are never written to disk), declared type, the
`%PDF` signature, that PyMuPDF opens it *as a PDF*, password protection, page
count, and that at least one page has selectable text. Accepted files are
copied to a private temporary file under a server-chosen name and deleted when
the request ends, whether it succeeds or fails.

## Python and Django support

- Pins: Django **5.2.18 (LTS**, security support to April 2028), DRF 3.17.2,
  django-cors-headers 4.8.0, openai 1.96.1, PyMuPDF 1.26.3.
- Supported Python: **3.10–3.14** (the intersection of the pins' declared
  support). Verified on 3.14.7 only.
- Before F5 the pin was Django 5.0.4: end of support since April 2025, officially
  Python 3.10–3.12 only, and on Python 3.14 any 5xx response with DEBUG off
  crashed inside Django's error reporting. 5.2 is the narrowest supported line
  that covers 3.14.

## Database

SQLite (`db.sqlite3` beside `manage.py`).

- **Adequate for:** local use and a small single-host demo. Writes are short
  (one deck row plus one bulk insert, in a transaction); the slow part of a
  request is the model calls, which hold no database lock.
- **Limits:** one host only (the file is local); writers are serialised, so many
  simultaneous generations can hit "database is locked" after SQLite's 5 s busy
  timeout; ephemeral container filesystems lose the file on redeploy unless it
  sits on a persistent volume.
- **Portability:** the code uses only the ORM (no raw SQL or SQLite-only
  features; `JSONField` and `Case/When` ordering work on PostgreSQL too). String
  values are trimmed to their column sizes, so a stricter database won't reject
  long names. Moving to PostgreSQL needs only a `DATABASES` change and a driver.

## Deployment caveats

- **Not deployed or load-tested.** No Dockerfile, process manager or CI is
  included.
- **TLS is the host's job.** HSTS and the HTTPS redirect are opt-in. `check
  --deploy` is clean with `DJANGO_SECURE_SSL_REDIRECT=true`,
  `DJANGO_SECURE_HSTS_SECONDS=31536000` and the two domain-wide HSTS flags; with
  only the required variables it reports W004 (HSTS) and W008 (SSL redirect),
  and with HSTS but not the domain-wide flags it reports W005 and W021.
- **Throttling is best-effort.** The default `locmem` cache is per process and
  resets on restart: with N workers the per-IP rates are effectively N× and the
  daily budget is per worker. Use `FLASH_CACHE=database` for shared counters
  (its increments are not atomic, so concurrent bursts may slightly overshoot).
  IP-based limits can be evaded by IP rotation; the global daily budget is the
  cost ceiling. Behind a proxy, set `DJANGO_NUM_PROXIES` correctly — too high
  lets clients spoof their IP, unset makes every client share the proxy's IP.
- **Generation is synchronous.** A request can take tens of seconds and holds
  a worker; set the WSGI server's timeout above `OPENAI_TIMEOUT_SECONDS` times
  the number of sequential model calls, and expect a client that disconnects
  not to cancel the server-side work.
- **Untrusted PDFs** are parsed by PyMuPDF (MuPDF, native code). The size and
  page limits bound the work, but run the service with OS-level memory/CPU
  limits and keep PyMuPDF patched.
- **Reverse proxy:** also cap the request body size there
  (e.g. `client_max_body_size`) a little above `FLASH_MAX_UPLOAD_MB`.
- **Static files / admin:** the admin is disabled and no static files are
  served; nothing needs `collectstatic`.
