"""
core.py – library functions, no user prompts
PDF / DOCX / TXT  → chunks → cards → .json +.txt +.apkg
"""

from __future__ import annotations
import random, pathlib, pickle, logging
from typing import List

from driver import run_extraction           # your existing helpers
from flashcard_gen import build_deck        # unchanged
from flashcard_gen import _cards_from_chunk
                                            # used for JSON‑only mode

log = logging.getLogger(__name__)           # <- replace prints

# --------------------------------------------------------------------
def run_pdf_to_cards(
    path: pathlib.Path,
    *,
    max_tokens: int = 900,
    max_cards_per_chunk: int = 3,
    sample_chunks: int | None = None,
    cache: bool = True,
) -> list[dict]:
    """
    Return **list of card‑dicts** for a single document.

    No user interaction, no printing.
    """
    log.info("Extracting %s", path)
    chunks = run_extraction(path, max_tokens=max_tokens)

    if sample_chunks:
        chunks = random.sample(chunks, min(sample_chunks, len(chunks)))
        log.info("Sampling %s random chunks", len(chunks))

    if cache:
        cache_path = path.with_suffix(".chunks.pkl")
        cache_path.write_bytes(pickle.dumps(chunks))
        log.info("Chunks cached → %s", cache_path.name)

    # generate cards JSON only (no Anki deck)
    cards: list[dict] = []
    for ch in chunks:
        cards.extend(_cards_from_chunk(ch, max_cards_per_chunk))

    return cards


# --------------------------------------------------------------------
def build_deck_files(
    path: pathlib.Path,
    *,
    max_tokens: int = 900,
    max_cards_per_chunk: int = 3,
    sample_chunks: int | None = None,
) -> tuple[pathlib.Path, pathlib.Path]:
    """
    Full pipeline helper used by CLI:
    returns (deck_path, json_path).
    """
    chunks = run_extraction(path, max_tokens=max_tokens)
    if sample_chunks:
        chunks = random.sample(chunks, min(sample_chunks, len(chunks)))

    deck_name = path.stem + ("_TEST" if sample_chunks else "")
    deck_path = build_deck(chunks, deck_name, max_cards_per_chunk)
    json_path = deck_path.with_suffix(".cards.json")

    return deck_path, json_path
