# flashcards/ai/driver.py
from __future__ import annotations
from pathlib import Path
import logging
from .ingest import extract_text
from .chunker import make_chunks

log = logging.getLogger(__name__)


def run_extraction(path: Path, *, max_tokens: int = 500) -> list[str]:
    pages = extract_text(path)                    # already list[(text,page)]
    chunks = make_chunks(pages, max_tokens=max_tokens)
    log.info("driver: %s chunk(s) from %s", len(chunks), path.name)
    return chunks
