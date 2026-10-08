"""
F5: uploads are validated on the server, whatever the client's picker did.
Synthetic files only; each rejection is a 4xx with a fixed message and code,
leaves no temporary file behind, and never reaches the model.
"""
from __future__ import annotations

import pathlib

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from flashcards.models import Deck
from flashcards.tests.fakes import make_blank_pdf, make_encrypted_pdf, make_pdf
from flashcards.tests.test_pipeline import ANALYZE, GENERATE, PipelineTestCase

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
TEXT = b"Chapter 1. Cells are the basic unit of life.\n" * 20


def padded_pdf(size: int) -> bytes:
    """A real one-page PDF followed by padding (still opens) to reach `size` bytes."""
    data = make_pdf(1)
    return data + b"\n%" + b"x" * max(0, size - len(data) - 2)


class UploadValidationTests(PipelineTestCase):
    def post(self, url, data: bytes | None, name="notes.pdf", content_type="application/pdf"):
        payload = {} if data is None else {"file": SimpleUploadedFile(name, data, content_type=content_type)}
        if url == GENERATE:
            payload["deck_name"] = "notes"
        return self.client.post(url, payload, format="multipart")

    def assertRejected(self, res, status, code):
        self.assertEqual(res.status_code, status, res.content)
        body = res.json()
        self.assertEqual(body["code"], code)
        self.assertTrue(body["detail"])
        for leak in (self.tmpdir, self.tmpdir.replace("\\", "/"), "Traceback", "flash-upload-", "fitz", "Error("):
            self.assertNotIn(leak, body["detail"])
        self.assertEqual(list(pathlib.Path(self.tmpdir).iterdir()), [], "temporary file left behind")
        return body

    def both(self, *args, status, code, **kw):
        for url in (ANALYZE, GENERATE):
            with self.subTest(url=url):
                self.assertRejected(self.post(url, *args, **kw), status, code)
        self.assertEqual(self.fake.calls, [], "a rejected upload reached the model")
        self.assertEqual(Deck.objects.count(), 0)

    def test_missing_file(self):
        self.both(None, status=400, code="file_required")

    def test_empty_file(self):
        self.both(b"", status=400, code="not_pdf")

    def test_declared_non_pdf_type_is_rejected_even_with_pdf_bytes(self):
        self.both(make_pdf(1), name="notes.txt", content_type="text/plain", status=400, code="not_pdf")
        self.both(make_pdf(1), name="picture.png", content_type="image/png", status=400, code="not_pdf")

    def test_non_pdf_content_with_pdf_name_and_type_is_rejected(self):
        # The F0 audit saw a .txt analysed "successfully"; it is now refused.
        self.both(TEXT, name="notes.pdf", status=400, code="not_pdf")
        self.both(PNG, name="scan.pdf", status=400, code="not_pdf")

    def test_signature_without_a_parseable_document(self):
        self.both(b"%PDF-1.7\n" + b"\x00garbage" * 50, status=400, code="pdf_unreadable")

    def test_password_protected_pdf(self):
        self.both(make_encrypted_pdf(), status=400, code="pdf_encrypted")

    def test_pdf_without_selectable_text(self):
        self.both(make_blank_pdf(3), status=400, code="no_text")

    @override_settings(FLASH_MAX_PDF_PAGES=3)
    def test_page_limit(self):
        res = self.post(ANALYZE, make_pdf(4))
        body = self.assertRejected(res, 400, "too_many_pages")
        self.assertEqual((body["pages"], body["limit_pages"]), (4, 3))
        self.both(make_pdf(4), status=400, code="too_many_pages")
        self.assertEqual(self.post(ANALYZE, make_pdf(3)).status_code, 200)

    @override_settings(FLASH_MAX_UPLOAD_MB=1)
    def test_size_limit(self):
        over = padded_pdf(1024 * 1024 + 4096)
        body = self.assertRejected(self.post(ANALYZE, over), 413, "file_too_large")
        self.assertEqual(body["limit_mb"], 1)
        self.both(over, status=413, code="file_too_large")
        # Just under the limit is accepted (the padding keeps it a valid PDF).
        under = padded_pdf(1024 * 1024 - 4096)
        self.assertEqual(self.post(ANALYZE, under).status_code, 200)

    @override_settings(FLASH_MAX_UPLOAD_MB=1, FILE_UPLOAD_MAX_MEMORY_SIZE=1024)
    def test_disk_spooled_uploads_are_cleaned_up_too(self):
        # Over FILE_UPLOAD_MAX_MEMORY_SIZE Django spools the upload to a temp
        # file of its own; accepted and rejected uploads both leave nothing.
        self.assertEqual(self.post(ANALYZE, padded_pdf(200_000)).status_code, 200)
        self.assertEqual(list(pathlib.Path(self.tmpdir).iterdir()), [])
        self.assertRejected(self.post(ANALYZE, padded_pdf(2 * 1024 * 1024)), 413, "file_too_large")

    def test_wrong_media_type(self):
        res = self.client.post(ANALYZE, {"file": "x"}, format="json")
        self.assertEqual(res.status_code, 415)
        self.assertEqual(res.json()["code"], "unsupported_media_type")

    def test_inspect_alias_is_validated_too(self):
        res = self.post("/api/flashcards/inspect/", TEXT)
        self.assertRejected(res, 400, "not_pdf")

    @override_settings(FLASH_MAX_UPLOAD_MB=1)
    def test_size_handler_stops_storing_oversized_uploads(self):
        # The first upload handler refuses bytes past the limit (so an
        # oversized body is never written to disk) and flags the request.
        from types import SimpleNamespace
        from django.core.files.uploadhandler import StopUpload
        from flashcards.uploads import SizeLimitUploadHandler

        req = SimpleNamespace()
        handler = SizeLimitUploadHandler(req)
        chunk = b"x" * (64 * 1024)
        self.assertEqual(handler.receive_data_chunk(chunk, 0), chunk)
        with self.assertRaises(StopUpload) as stop:
            handler.receive_data_chunk(chunk, 1024 * 1024 - 10)
        self.assertFalse(stop.exception.connection_reset)  # the client still gets the 413
        self.assertTrue(req.flash_upload_too_large)

    def test_a_valid_pdf_still_analyzes(self):
        res = self.post(ANALYZE, make_pdf(2))
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()["pages"], 2)
        self.assertEqual(list(pathlib.Path(self.tmpdir).iterdir()), [])
