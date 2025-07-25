"""
driver.py – tiny wrapper around ingest+chunker
"""
import logging
from pathlib import Path
from .ingest import extract_text
from .chunker import make_chunks

log = logging.getLogger(__name__)


def run_extraction(path: Path, *, max_tokens: int = 900) -> list[str]:
    raw   = extract_text(path)
    parts = make_chunks(raw, max_tokens=max_tokens)
    log.info("driver: %s chunks from %s", len(parts), path.name)
    return parts
