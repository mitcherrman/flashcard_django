# flashcards/ai/ingest.py
"""
ingest.py – read a PDF / DOCX / TXT and return
List[Tuple[str, int]] →  [(page_text, page_no), …]
"""
from __future__ import annotations
from pathlib import Path
from typing import List, Tuple
import logging
import fitz  #  PyMuPDF

log = logging.getLogger(__name__)


def extract_text(path: Path) -> List[Tuple[str, int]]:
    """
    Extract every page with PyMuPDF.

    Returns
    -------
    list[tuple[str, int]]
        e.g.  [("First page text …", 1),
               ("Second page text …", 2),
               …]
    Raises
    ------
    RuntimeError
        If the file type is unsupported or PyMuPDF fails.
    """
    if path.suffix.lower() != ".pdf":
        raise RuntimeError("Only .pdf files supported in this ingest module")

    pages: list[tuple[str, int]] = []
    with fitz.open(path) as doc:
        for page_no in range(doc.page_count):
            page = doc.load_page(page_no)
            text = page.get_text("text")          # “text” = simple UTF‑8
            pages.append((text.strip(), page_no + 1))

    log.info("ingest → %s page(s) from %s", len(pages), path.name)
    return pages
