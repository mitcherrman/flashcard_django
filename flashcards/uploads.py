"""
Server-side validation of uploaded PDFs. The client's picker is a
convenience, not a control: everything here assumes an untrusted upload.

`receive_pdf(request)` checks, in order:
  1. size (413 file_too_large; see SizeLimitUploadHandler),
  2. presence (400 file_required),
  3. declared type and the %PDF signature (400 not_pdf),
  4. that PyMuPDF opens it *as a PDF* (400 pdf_unreadable / pdf_encrypted),
  5. page count (400 too_many_pages) and selectable text (400 no_text),
and returns a ReceivedPdf: a private temporary copy under a server-chosen
name. `ReceivedPdf.process(fn)` runs the real work on it and always removes
the copy afterwards — on success, on an API error and on any other failure.

Windows note: an exception raised inside PyMuPDF keeps its document (and the
open file handle) alive through the traceback, and an open file can't be
deleted there. So errors are raised only after the failing call's frames
have been released, and documents are closed in `finally` blocks.
"""
from __future__ import annotations

import gc
import logging
import os
import pathlib
import tempfile
import traceback
from dataclasses import dataclass
from typing import Callable, TypeVar

import fitz  # PyMuPDF
from django.conf import settings
from django.core.files.uploadhandler import FileUploadHandler, StopUpload
from rest_framework.exceptions import APIException

from .errors import SERVER_ERROR, ApiError

log = logging.getLogger(__name__)

T = TypeVar("T")

# Browsers and native pickers label PDFs inconsistently; anything else that
# is declared (text/plain, image/png, …) is rejected. The signature check
# below is what actually decides.
ACCEPTED_TYPES = {"", "application/pdf", "application/x-pdf", "application/octet-stream", "binary/octet-stream"}
SIGNATURE = b"%PDF-"
SIGNATURE_WINDOW = 1024  # the PDF header may follow up to 1 KB of junk


def max_upload_bytes() -> int:
    return int(settings.FLASH_MAX_UPLOAD_MB) * 1024 * 1024


class SizeLimitUploadHandler(FileUploadHandler):
    """
    First upload handler: stops storing an upload once it passes the size
    limit. The rest of the body is read and discarded (connection_reset=False)
    so the client still receives the 413 instead of a dropped connection.
    """

    def receive_data_chunk(self, raw_data, start):
        if start + len(raw_data) > max_upload_bytes():
            if self.request is not None:
                self.request.flash_upload_too_large = True
            raise StopUpload(connection_reset=False)
        return raw_data

    def file_complete(self, file_size):
        return None  # the next handler builds the file


def too_large() -> ApiError:
    mb = int(settings.FLASH_MAX_UPLOAD_MB)
    return ApiError(413, "file_too_large", f"The file is larger than the {mb} MB limit.", limit_mb=mb)


def not_pdf(detail: str = "The file isn't a PDF.") -> ApiError:
    return ApiError(400, "not_pdf", detail)


def unreadable(detail: str = "This file couldn't be read as a PDF. It may be damaged.") -> ApiError:
    return ApiError(400, "pdf_unreadable", detail)


def _remove(path: pathlib.Path) -> None:
    for attempt in (1, 2):
        try:
            path.unlink(missing_ok=True)
            return
        except OSError:
            if attempt == 1:
                gc.collect()  # release a PyMuPDF document still holding the file
    log.warning("could not remove temporary file %s", path.name)


@dataclass(frozen=True)
class ReceivedPdf:
    path: pathlib.Path
    name: str
    pages: int

    def discard(self) -> None:
        # The pipeline may leave a chunk cache beside the upload; remove both.
        for p in (self.path, self.path.with_suffix(".chunks.pkl")):
            _remove(p)

    def process(self, fn: Callable[[pathlib.Path], T]) -> T:
        """
        Return fn(path), then discard the file. API errors pass through; any
        other failure is logged and answered as a generic 500 (raised after
        the failing frames are gone, so the file can be deleted).
        """
        failed = False
        try:
            return fn(self.path)
        except APIException:
            raise
        except Exception:
            # Logged as text: a log record holding exc_info would keep the
            # failing frames (and any open document) alive.
            log.error("request failed while processing an upload\n%s", traceback.format_exc())
            failed = True
        finally:
            if not failed:
                self.discard()
        self.discard()
        raise ApiError(500, "server_error", SERVER_ERROR)


def _check_document(path: pathlib.Path) -> int:
    doc = None
    try:
        doc = fitz.open(path.as_posix(), filetype="pdf")
    except Exception:
        log.info("upload rejected: PyMuPDF could not open it as a PDF", exc_info=True)
    if doc is None:
        raise unreadable()
    try:
        if doc.needs_pass:
            raise ApiError(400, "pdf_encrypted", "This PDF is password-protected.")
        pages = doc.page_count
        if pages < 1:
            raise unreadable("This file couldn't be read as a PDF. It has no pages.")
        limit = int(settings.FLASH_MAX_PDF_PAGES)
        if pages > limit:
            raise ApiError(400, "too_many_pages", f"This PDF has {pages} pages; the limit is {limit}.",
                           limit_pages=limit, pages=pages)
        has_text = None
        try:
            has_text = any((doc.load_page(i).get_text("text") or "").strip() for i in range(pages))
        except Exception:
            log.info("upload rejected: page text extraction failed", exc_info=True)
        if has_text is None:
            raise unreadable()
        if not has_text:
            raise ApiError(400, "no_text", "This PDF has no selectable text. It may be a scanned image.")
        return pages
    finally:
        doc.close()


def receive_pdf(request) -> ReceivedPdf:
    files = request.FILES  # parses the body; the size handler runs here
    if getattr(request, "flash_upload_too_large", False):
        raise too_large()
    up = files.get("file")
    if up is None:
        raise ApiError(400, "file_required", "No file was uploaded.")
    if up.size > max_upload_bytes():
        raise too_large()
    if up.size == 0:
        raise not_pdf("The file is empty.")
    declared = (up.content_type or "").split(";")[0].strip().lower()
    if declared not in ACCEPTED_TYPES:
        raise not_pdf()
    head = up.read(SIGNATURE_WINDOW)
    up.seek(0)
    if SIGNATURE not in head:
        raise not_pdf()

    # A private copy for PyMuPDF and the pipeline, under a server-chosen name.
    fd, raw_path = tempfile.mkstemp(prefix="flash-upload-", suffix=".pdf")
    path = pathlib.Path(raw_path)
    ok = False
    try:
        with os.fdopen(fd, "wb") as tmp:
            for chunk in up.chunks():
                tmp.write(chunk)
        pages = _check_document(path)
        ok = True
    finally:
        if not ok:
            _remove(path)
    return ReceivedPdf(path=path, name=pathlib.Path(up.name or "document.pdf").name, pages=pages)
