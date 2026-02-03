# flashcards/views_async.py
from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from django.conf import settings
from django.core.cache import cache
from django.core.files.uploadedfile import UploadedFile
from django.http import JsonResponse, HttpRequest
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from celery.result import AsyncResult

from .tasks import build_template_task, build_deck_task

log = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Utilities
# ──────────────────────────────────────────────────────────────────────────────

def _progress_key(task_id: str) -> str:
    return f"task_progress:{task_id}"

def _get_progress(task_id: str) -> str:
    return cache.get(_progress_key(task_id), "")

def _save_uploaded_to_tmp(request: HttpRequest) -> tuple[str, str]:
    """
    Save uploaded file to MEDIA_ROOT/tmp and return (tmp_path, original_filename).
    """
    if "file" not in request.FILES:
        raise ValueError("No file field found in upload")

    up = request.FILES["file"]
    orig_name = getattr(up, "name", "document.pdf")

    tmp_dir = Path(getattr(settings, "MEDIA_ROOT", Path.cwd())) / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    suffix = Path(orig_name).suffix or ".pdf"
    fd, tmp_path = tempfile.mkstemp(prefix="upload_", suffix=suffix, dir=str(tmp_dir))
    with os.fdopen(fd, "wb") as f:
        for chunk in up.chunks():
            f.write(chunk)

    return tmp_path, orig_name


# ──────────────────────────────────────────────────────────────────────────────
# Template build API (analyze + section detection)
# ──────────────────────────────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(["POST"])
def template_build(request: HttpRequest) -> JsonResponse:
    try:
        tmp_path, filename = _save_uploaded_to_tmp(request)
        task = build_template_task.delay(file_path=tmp_path, filename=filename)
        return JsonResponse({"job_id": task.id})
    except Exception as e:
        log.exception("template_build failed")
        return JsonResponse({"detail": str(e)}, status=400)

@require_http_methods(["GET"])
def template_status(request: HttpRequest, task_id: str) -> JsonResponse:
    res = AsyncResult(task_id)
    status = res.status
    data: Dict[str, Any] = {"status": status, "progress": _get_progress(task_id)}

    if status == "SUCCESS":
        data["result"] = res.get(propagate=False)
    elif status == "FAILURE":
        # res.result is usually an Exception
        data["error"] = str(res.result)

    return JsonResponse(data)

@require_http_methods(["GET"])
def template_check(request: HttpRequest) -> JsonResponse:
    """
    Optional fast path: return cached sections if you store by filename.
    For now, always return empty so the client will call /template/build/.
    """
    # filename = request.GET.get("filename") or ""
    return JsonResponse({"sections": []})


# ──────────────────────────────────────────────────────────────────────────────
# Deck build API (card generation)
# ──────────────────────────────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(["POST"])
def start_build_deck(request: HttpRequest) -> JsonResponse:
    """
    Launch async deck build from a PDF and an allocation plan.

    multipart/form-data:
      - file: PDF
      - allocations: JSON array of {title, page_start, page_end, cards}
      - coverage: "even" | "section"
      - cards_wanted: int (optional)
      - deck_name: optional
    """
    f: Optional[UploadedFile] = request.FILES.get("file")
    if not f:
        return JsonResponse({"error": "file missing"}, status=400)

    # Parse fields
    try:
        allocations = json.loads(request.POST.get("allocations") or "[]")
    except json.JSONDecodeError:
        return JsonResponse({"error": "allocations must be JSON"}, status=400)

    coverage     = (request.POST.get("coverage") or "even").strip()
    cards_wanted = int(request.POST.get("cards_wanted") or 0) or None
    deck_name    = request.POST.get("deck_name") or f.name or "New Deck"

    # Persist upload to a worker-readable tmp path
    tmp_dir = Path(tempfile.mkdtemp(prefix="deckbuild_"))
    pdf_path = str(tmp_dir / (f.name or "document.pdf"))
    with open(pdf_path, "wb") as out:
        for chunk in f.chunks():
            out.write(chunk)

    # Associate user if authenticated
    user_id = request.user.id if (getattr(request, "user", None) and request.user.is_authenticated) else None

    # Enqueue Celery job
    async_res = build_deck_task.delay(
        pdf_path=pdf_path,
        deck_name=deck_name,
        allocations=allocations,
        coverage=coverage,
        cards_wanted=cards_wanted,
        user_id=user_id,
    )
    return JsonResponse({"job_id": async_res.id})

@require_http_methods(["GET"])
def build_deck_status(request: HttpRequest, job_id: str) -> JsonResponse:
    """
    Poll a deck build job.
    Response:
      { status, progress?, result?: { deck_id, cards_count, created } | error? }
    """
    res = AsyncResult(job_id)
    payload: Dict[str, Any] = {"status": res.status}

    # Prefer the progress we stored in cache; fall back to Celery meta if present.
    progress = _get_progress(job_id)
    if not progress and isinstance(res.info, dict):
        progress = res.info.get("progress", "")
    if progress:
        payload["progress"] = progress

    if res.status == "FAILURE":
        payload["error"] = str(res.info)
    elif res.status == "SUCCESS":
        result = res.result if isinstance(res.result, dict) else {}
        payload["result"] = {
            "deck_id": result.get("deck_id"),
            "cards_count": result.get("cards_count"),
            "created": result.get("created", True),
        }

    return JsonResponse(payload)
