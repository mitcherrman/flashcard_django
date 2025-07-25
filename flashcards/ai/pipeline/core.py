"""
flashcards.ai.pipeline.core
———————————
Pure‑library helpers – **NO Django, NO prints**.

• cards_from_document(path, …) → list[dict]   (for views.generate_deck)
• write_json_for_document(path, …) → Path     (optional utility)

Both functions rely only on the low‑level helpers in the ai package.
"""

from __future__ import annotations
import pathlib, random, pickle, json, logging
from typing import List
from ..driver         import run_extraction
from ..flashcard_gen  import _cards_from_chunk               # <- only this

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# 1)  High‑level helper used by the Django view                               #
# --------------------------------------------------------------------------- #
def cards_from_document(
    path: pathlib.Path,
    *,
    max_tokens: int        = 900,
    cards_per_chunk: int   = 3,
    sample_chunks: int | None = None,
    cache_chunks: bool     = True,
) -> List[dict]:
    """
    Turn a PDF / DOCX / TXT into **list[card‑dict]**.

    No printing – the caller (views.generate_deck) does logging / error handling.
    """
    chunks = run_extraction(path, max_tokens=max_tokens)
    if sample_chunks:
        chunks = random.sample(chunks, min(sample_chunks, len(chunks)))
        log.debug("Sampled %s random chunks", len(chunks))

    if cache_chunks:
        cache = path.with_suffix(".chunks.pkl")
        cache.write_bytes(pickle.dumps(chunks))
        log.debug("Chunk cache written → %s", cache.name)

    cards: list[dict] = []
    for ch in chunks:
        cards.extend(_cards_from_chunk(ch, cards_per_chunk))
    return cards


# --------------------------------------------------------------------------- #
# 2)  Convenience helper – not used by Django views, but kept for CLI/tests   #
# --------------------------------------------------------------------------- #
def write_json_for_document(
    path: pathlib.Path,
    *,
    max_tokens: int      = 900,
    cards_per_chunk: int = 3,
    sample_chunks: int | None = None,
) -> pathlib.Path:
    """
    Generate cards, write <file>.cards.json next to the document,
    return the Path.
    """
    cards = cards_from_document(
        path,
        max_tokens=max_tokens,
        cards_per_chunk=cards_per_chunk,
        sample_chunks=sample_chunks,
        cache_chunks=False,
    )
    out = path.with_suffix(".cards.json")
    out.write_text(json.dumps(cards, ensure_ascii=False, indent=2), encoding="utf‑8")
    log.info("Card JSON written → %s (%s cards)", out.name, len(cards))
    return out
