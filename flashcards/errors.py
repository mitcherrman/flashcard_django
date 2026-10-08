"""
The API's error contract.

Every error response is JSON with a user-safe message and a stable code:

    {"detail": "<message a user can read>", "code": "<machine code>", ...}

Some codes add a field (e.g. `limit`). Exception text, file names and
server paths never reach the client: unexpected failures are logged with
their traceback and answered with a generic 500.

    400  file_required, not_pdf, pdf_unreadable, pdf_encrypted, too_many_pages,
         no_text, invalid_allocations, deck_id_required, parse_error, bad_request
    403  forbidden (Django-level only; no API view denies access today)
    404  deck_not_found, not_found
    405  method_not_allowed
    413  file_too_large
    415  unsupported_media_type
    429  throttled, generation_limit_reached        (+ Retry-After header)
    500  server_error
    502  generation_failed
    503  generation_unavailable
"""
from __future__ import annotations

import logging

from django.http import JsonResponse
from rest_framework import status
from rest_framework.exceptions import APIException, Throttled
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

log = logging.getLogger(__name__)

SERVER_ERROR = "Something went wrong on the server. Try again in a moment."


class ApiError(APIException):
    """An expected failure with a fixed, user-safe message."""

    def __init__(self, status_code: int, code: str, detail: str, **extra):
        self.status_code = status_code
        self.extra = extra
        super().__init__(detail=detail, code=code)


class GenerationLimitReached(Throttled):
    """The server-wide daily generation budget is spent."""

    default_code = "generation_limit_reached"

    def __init__(self, wait: float):
        super().__init__(wait=wait, detail="This demo has reached its daily limit for new decks.")


def _wait_text(seconds: float | None) -> str:
    if not seconds:
        return "Try again later."
    minutes = max(1, round(seconds / 60))
    if minutes < 90:
        return f"Try again in about {minutes} minute{'s' if minutes != 1 else ''}."
    hours = round(minutes / 60)
    return f"Try again in about {hours} hours."


def _code_of(exc: APIException) -> str:
    codes = exc.get_codes()
    return codes if isinstance(codes, str) else exc.default_code


def api_exception_handler(exc, context):
    response = drf_exception_handler(exc, context)
    if response is None:
        # Not an API exception: a bug or an unexpected library failure.
        log.error("Unhandled error in %s", context.get("view").__class__.__name__, exc_info=exc)
        return Response({"detail": SERVER_ERROR, "code": "server_error"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    code = _code_of(exc) if isinstance(exc, APIException) else "error"
    if isinstance(exc, Throttled):
        if code == "throttled":
            detail = "Too many requests from this device."
        else:
            detail = str(exc.detail).split(" Expected available")[0]
        body = {"detail": f"{detail} {_wait_text(exc.wait)}", "code": code}
        if exc.wait is not None:
            body["retry_after"] = int(exc.wait)
    elif isinstance(exc, ApiError):
        body = {"detail": str(exc.detail), "code": code, **exc.extra}
    elif isinstance(exc.detail, (list, dict)):
        # DRF validation-style payloads: keep them out of the primary message.
        body = {"detail": "The request was not valid.", "code": "invalid"}
    else:
        body = {"detail": str(exc.detail), "code": code}
    response.data = body
    return response


# Django-level handlers (outside the API views: unknown URLs, a disallowed
# Host header, …), so every answer is JSON.
def bad_request(request, exception=None):
    return JsonResponse({"detail": "Bad request.", "code": "bad_request"}, status=400)


def forbidden(request, exception=None):
    return JsonResponse({"detail": "Forbidden.", "code": "forbidden"}, status=403)


def not_found(request, exception=None):
    return JsonResponse({"detail": "Not found.", "code": "not_found"}, status=404)


def server_error(request):
    return JsonResponse({"detail": SERVER_ERROR, "code": "server_error"}, status=500)
