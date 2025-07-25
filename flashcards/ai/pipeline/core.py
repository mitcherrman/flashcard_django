"""
core.py – library helpers for Django views
"""
from __future__ import annotations
import pathlib, random, pickle, logging
from typing import List
from ..driver import run_extraction
from ..flashcard_gen import build_json, _cards_from_chunk

log = logging.getLogger(__name__)


def cards_from_document(
    path: pathlib.Path,
    *,
    max_tokens: int = 900,
    cards_per_chunk: int = 3,
    sample_chunks: int | None = None,
    cache_chunks: bool = True,
) -> List[dict]:
    chunks = run_extraction(path, max_tokens=max_tokens)
    if sample_chunks:
        chunks = random.sample(chunks, min(sample_chunks, len(chunks)))
        log.info("Sampling %s random chunk(s)", len(chunks))

    if cache_chunks:
        path.with_suffix(".chunks.pkl").write_bytes(pickle.dumps(chunks))

    cards: list[dict] = []
    for ch in chunks:
        cards.extend(_cards_from_chunk(ch, cards_per_chunk))
    return cards


def write_json_for_document(
    path: pathlib.Path,
    *,
    max_tokens: int = 900,
    cards_per_chunk: int = 3,
    sample_chunks: int | None = None,
) -> pathlib.Path:
    chunks = run_extraction(path, max_tokens=max_tokens)
    if sample_chunks:
        chunks = random.sample(chunks, min(sample_chunks, len(chunks)))
    return build_json(chunks, deck_name=path.stem, max_cards_per_chunk=cards_per_chunk)
