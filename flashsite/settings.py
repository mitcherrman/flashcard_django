"""
Django settings for flashsite.

Every deployment-sensitive value comes from the environment (or a local,
gitignored `.env` file read by python-decouple). See README.md and
.env.example for the full list.

Safe defaults:
  * DJANGO_DEBUG is off unless explicitly enabled.
  * With DEBUG off, DJANGO_SECRET_KEY and DJANGO_ALLOWED_HOSTS are required;
    the process refuses to start without them (ImproperlyConfigured).
  * With DEBUG on (local development), a fixed dev-only secret key, the
    localhost host names and any localhost CORS origin are allowed.

An empty variable counts as unset, so `VAR=` can be used to clear a value
inherited from `.env`.
"""

from pathlib import Path

from decouple import Csv, config
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def _flag(name: str, default: bool) -> bool:
    raw = config(name, default="")
    if raw.strip() == "":
        return default
    value = raw.strip().lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    raise ImproperlyConfigured(f"{name} must be true or false, not {raw!r}.")


def _int(name: str, default: int | None) -> int | None:
    raw = config(name, default="").strip()
    if raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise ImproperlyConfigured(f"{name} must be a whole number, not {raw!r}.") from None


def _list(name: str) -> list[str]:
    return [v for v in config(name, default="", cast=Csv()) if v]


# ── Core ─────────────────────────────────────────────────────────────────────
DEBUG = _flag("DJANGO_DEBUG", False)

_DEV_SECRET_KEY = "django-insecure-local-development-only-never-use-in-production"
SECRET_KEY = config("DJANGO_SECRET_KEY", default="").strip()
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured(
            "DJANGO_SECRET_KEY is required when DJANGO_DEBUG is off. "
            "For local development set DJANGO_DEBUG=true (see .env.example)."
        )
    SECRET_KEY = _DEV_SECRET_KEY
elif not DEBUG and (SECRET_KEY.startswith("django-insecure-") or len(SECRET_KEY) < 50 or len(set(SECRET_KEY)) < 5):
    raise ImproperlyConfigured(
        "DJANGO_SECRET_KEY is too weak for DJANGO_DEBUG=false: use at least 50 random "
        "characters, not a 'django-insecure-' key."
    )

ALLOWED_HOSTS = _list("DJANGO_ALLOWED_HOSTS")
if not ALLOWED_HOSTS:
    if not DEBUG:
        raise ImproperlyConfigured(
            "DJANGO_ALLOWED_HOSTS is required when DJANGO_DEBUG is off "
            "(comma-separated host names, e.g. api.example.com)."
        )
    # Local development. For a phone on the LAN, add the dev machine's IP.
    ALLOWED_HOSTS = ["localhost", ".localhost", "127.0.0.1", "[::1]"]
elif not DEBUG and "*" in ALLOWED_HOSTS:
    raise ImproperlyConfigured("DJANGO_ALLOWED_HOSTS must list explicit host names when DJANGO_DEBUG is off.")


# ── Application definition ───────────────────────────────────────────────────
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'corsheaders',
    'rest_framework',
    'flashcards',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'flashsite.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'flashsite.wsgi.application'


# ── CORS (frontend and backend may be hosted on different origins) ───────────
# The API uses no cookies or other credentials, so cross-origin requests are
# never credentialed and CSRF has no ambient credential to abuse.
CORS_ALLOWED_ORIGINS = _list("DJANGO_CORS_ALLOWED_ORIGINS")
if "*" in CORS_ALLOWED_ORIGINS:
    raise ImproperlyConfigured("DJANGO_CORS_ALLOWED_ORIGINS must list explicit origins, not '*'.")
CORS_ALLOWED_ORIGIN_REGEXES = (
    # Local development: Expo web (8081/19006) or a static export on any port.
    [r"^http://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$"] if DEBUG and not CORS_ALLOWED_ORIGINS else []
)
CORS_ALLOW_ALL_ORIGINS = False
CORS_ALLOW_CREDENTIALS = False
CORS_URLS_REGEX = r"^/api/.*$"
CORS_EXPOSE_HEADERS = ["Retry-After"]


# ── REST framework ───────────────────────────────────────────────────────────
REST_FRAMEWORK = {
    # No accounts exist: every request is anonymous and no session cookie is
    # consulted. Unsafe methods stay closed unless a view opts in (AllowAny).
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticatedOrReadOnly",
    ],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "EXCEPTION_HANDLER": "flashcards.errors.api_exception_handler",
    # Throttles identify clients by IP. Behind a reverse proxy, set
    # DJANGO_NUM_PROXIES so the client IP is read from X-Forwarded-For.
    "NUM_PROXIES": _int("DJANGO_NUM_PROXIES", None),
}

# Per-client-IP rates (flashcards/throttles.py): "<count>/<second|minute|hour|day>".
FLASH_THROTTLE_RATES = {
    "analyze": config("FLASH_THROTTLE_ANALYZE", default="").strip() or "30/hour",
    "generate": config("FLASH_THROTTLE_GENERATE", default="").strip() or "10/hour",
    "read": config("FLASH_THROTTLE_READ", default="").strip() or "120/minute",
}
# Generations allowed per UTC day across ALL clients: a cost ceiling that
# per-IP limits cannot give. 0 turns generation off.
FLASH_GENERATION_DAILY_LIMIT = _int("FLASH_GENERATION_DAILY_LIMIT", 100)

# Throttle counters live in the default cache. "locmem" (default) is
# per-process and resets on restart; "database" is shared by every worker
# and survives restarts (run `python manage.py createcachetable` once).
_CACHE = config("FLASH_CACHE", default="").strip().lower() or "locmem"
if _CACHE == "locmem":
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "flash"}}
elif _CACHE == "database":
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.db.DatabaseCache", "LOCATION": "flash_cache"}}
else:
    raise ImproperlyConfigured("FLASH_CACHE must be 'locmem' or 'database'.")


# ── Uploads ──────────────────────────────────────────────────────────────────
FLASH_MAX_UPLOAD_MB = _int("FLASH_MAX_UPLOAD_MB", 20)
FLASH_MAX_PDF_PAGES = _int("FLASH_MAX_PDF_PAGES", 200)
FILE_UPLOAD_HANDLERS = [
    "flashcards.uploads.SizeLimitUploadHandler",  # first: stops oversized uploads
    "django.core.files.uploadhandler.MemoryFileUploadHandler",
    "django.core.files.uploadhandler.TemporaryFileUploadHandler",
]


# ── Model (OpenAI) ───────────────────────────────────────────────────────────
# Optional: without a key, /generate/ answers 503 and everything else works.
# The OpenAI SDK also reads OPENAI_BASE_URL (point it at a local stub to
# exercise generation without OpenAI).
OPENAI_API_KEY = config("OPENAI_API_KEY", default="").strip()
OPENAI_TIMEOUT_SECONDS = _int("OPENAI_TIMEOUT_SECONDS", 60)


# ── Database ─────────────────────────────────────────────────────────────────
# SQLite: fine for local use and a small single-host demo (see README).
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]


# ── Internationalization ─────────────────────────────────────────────────────
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True


# ── Static & media ───────────────────────────────────────────────────────────
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "uploads"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# ── HTTPS / cookies (production) ─────────────────────────────────────────────
# TLS is usually terminated by the host or a reverse proxy, so the redirect
# and HSTS are opt-in rather than assumed (see README, "Deployment caveats").
SECURE_SSL_REDIRECT = _flag("DJANGO_SECURE_SSL_REDIRECT", False)
SECURE_HSTS_SECONDS = _int("DJANGO_SECURE_HSTS_SECONDS", 0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = _flag("DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", False)
SECURE_HSTS_PRELOAD = _flag("DJANGO_SECURE_HSTS_PRELOAD", False)
if _flag("DJANGO_SECURE_PROXY_SSL_HEADER", False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG


# ── Logging ──────────────────────────────────────────────────────────────────
# Console only. INFO by default: DEBUG also logs model output excerpts.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": config("DJANGO_LOG_LEVEL", default="").strip() or "INFO"},
}
