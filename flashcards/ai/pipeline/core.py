# flashcards/ai/pipeline/core.py
"""
flashcards.ai.pipeline.core
———————————
Pure-library helpers – **NO Django, NO prints**.

• cards_from_document(path, …) → list[dict]   (for views.generate_deck)
• write_json_for_document(path, …) → Path     (optional utility)
"""

from __future__ import annotations
import pathlib, random, pickle, json, logging
from typing import List, Tuple
from ..driver         import run_extraction
from ..flashcard_gen  import _cards_from_chunk

log = logging.getLogger(__name__)


# ───────────────────────── helpers ─────────────────────────
def _normalize_chunks(raw_chunks) -> List[Tuple[str, int]]:
    """
    Accept either:
      • [str, str, …]                     (implicit pages 1..N), or
      • [(text, page_no), (text, page_no)]
    and return a list of (text, page_no).
    """
    norm: List[Tuple[str, int]] = []
    if not raw_chunks:
        return norm
    first = raw_chunks[0]
    if isinstance(first, tuple) and len(first) == 2:
        # Already (text, page)
        for txt, p in raw_chunks:
            norm.append((txt, int(p)))
    else:
        # Plain strings → assign 1..N
        for i, txt in enumerate(raw_chunks, start=1):
            norm.append((txt, i))
    return norm


def _distribute_quota(total: int, parts: int) -> list[int]:
    """Evenly split 'total' across 'parts' (first buckets get the extras)."""
    if parts <= 0:
        return []
    base, extra = divmod(total, parts)
    return [base + (1 if i < extra else 0) for i in range(parts)]


# ───────────────────── public API ──────────────────────────
def cards_from_document(
    path: pathlib.Path,
    *,
    # global control
    total_cards: int | None = None,         # None → uncapped (legacy behavior)
    max_cards_per_chunk: int = 3,
    # extraction / testing
    max_tokens: int = 500,
    sample_chunks: int | None = None,       # pick K random chunks (test/demo)
    cache_chunks: bool = True,              # write <file>.chunks.pkl
) -> List[dict]:
    """
    Turn a PDF / DOCX / TXT into **list[card-dict]**.

    If total_cards is provided, we cap globally and distribute requests fairly
    across chunks/pages. Otherwise we request up to max_cards_per_chunk for
    every chunk.
    """
    raw = run_extraction(path, max_tokens=max_tokens)
    chunks = _normalize_chunks(raw)                      # -> [(text, page)]
    log.info("core: %s chunk(s) ready", len(chunks))

    # Optional random sampling (for quick tests)
    if sample_chunks:
        k = min(sample_chunks, len(chunks))
        chunks = random.sample(chunks, k)
        log.debug("Sampled %s random chunk(s) for test/demo", len(chunks))

    # Optional cache of normalized chunks
    if cache_chunks:
        cache = path.with_suffix(".chunks.pkl")
        try:
            cache.write_bytes(pickle.dumps(chunks))
            log.debug("Chunk cache written → %s", cache.name)
        except Exception as e:
            log.warning("Could not write chunk cache %s: %s", cache, e)

    cards: list[dict] = []

    if total_cards is None:
        # Legacy behavior: per-chunk request only
        for chunk_txt, page_no in chunks:
            got = _cards_from_chunk(
                chunk_txt, page_no=page_no, max_cards=max_cards_per_chunk
            )
            cards.extend(got)
        return cards

    # NEW: global target distributed across chunks
    quotas = _distribute_quota(total_cards, len(chunks))
    for (chunk_txt, page_no), q in zip(chunks, quotas):
        if q <= 0:
            continue
        q = min(q, max_cards_per_chunk)  # respect per-chunk ceiling
        got = _cards_from_chunk(chunk_txt, page_no=page_no, max_cards=q)
        cards.extend(got)
        if len(cards) >= total_cards:
            break

    return cards[:total_cards]


def write_json_for_document(
    path: pathlib.Path,
    *,
    max_tokens: int = 500,
    total_cards: int | None = None,         # allow global cap in CLI, too
    max_cards_per_chunk: int = 3,
    sample_chunks: int | None = None,
) -> pathlib.Path:
    """
    Generate cards, write <file>.cards.json next to the document, return the Path.
    """
    cards = cards_from_document(
        path,
        max_tokens=max_tokens,
        total_cards=total_cards,
        max_cards_per_chunk=max_cards_per_chunk,
        sample_chunks=sample_chunks,
        cache_chunks=False,
    )
    out = path.with_suffix(".cards.json")
    out.write_text(json.dumps(cards, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Card JSON written → %s (%s cards)", out.name, len(cards))
    return out
