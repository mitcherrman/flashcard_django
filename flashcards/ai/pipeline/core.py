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
from typing import List, Tuple, Dict, Optional, Any
from ..driver         import run_extraction
from ..flashcard_gen  import _cards_from_chunk

log = logging.getLogger(__name__)

# ───────────────────────── helpers ─────────────────────────
def _pick_page_from(item_parts: List[Any], fallback: int) -> int:
    """
    Best-effort page inference from extra tuple/list parts or dicts.
    Looks for:
      • an int (assumed to be page_start)
      • a (start, end) tuple/list of ints → use start
      • dict with 'page' or 'page_start'
    """
    page: Optional[int] = None
    for elem in item_parts:
        if isinstance(elem, int):
            page = elem
            break
        if isinstance(elem, (tuple, list)) and len(elem) == 2 and all(isinstance(x, int) for x in elem):
            page = elem[0]
            break
        if isinstance(elem, dict):
            if "page" in elem and isinstance(elem["page"], int):
                page = elem["page"]
                break
            if "page_start" in elem and isinstance(elem["page_start"], int):
                page = elem["page_start"]
                break
    return int(page if page is not None else fallback)

def _normalize_chunks(raw_chunks) -> List[Tuple[str, int]]:
    """
    Accept any of:
      • ["text", "text2", …]                         → pages 1..N
      • [("text", 5), ("text2", 6), …]               → use given page
      • [("text", 5, "section"), …]                  → use first int as page
      • [[...], {...}]                               → same logic
    Returns: list of (text, page_no).
    """
    norm: List[Tuple[str, int]] = []
    if not raw_chunks:
        return norm

    for idx, item in enumerate(raw_chunks, start=1):
        # shape: tuple/list
        if isinstance(item, (tuple, list)) and item:
            txt = item[0]
            page = _pick_page_from(list(item[1:]), fallback=idx)
            norm.append((str(txt), page))
            continue

        # shape: dict with text/page (very defensive)
        if isinstance(item, dict):
            txt = item.get("text", "")
            page = item.get("page") or item.get("page_start") or idx
            norm.append((str(txt), int(page)))
            continue

        # plain string
        norm.append((str(item), idx))

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
    max_cards_per_chunk: int = 30,
    # extraction / testing
    max_tokens: int = 500,
    sample_chunks: int | None = None,       # pick K random chunks (test/demo)
    cache_chunks: bool = True,              # write <file>.chunks.pkl
    # explicit per-page quotas (page_no -> cards)
    per_page_quotas: Optional[Dict[int, int]] = None,
) -> List[dict]:
    """
    Turn a PDF / DOCX / TXT into **list[card-dict]**.

    If per_page_quotas is provided, we request exactly that many cards
    from each page. Otherwise, if total_cards is provided, we cap globally
    and distribute requests fairly across chunks/pages. If neither is given,
    we request up to max_cards_per_chunk for every chunk.
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

    # 1) Explicit per-page quotas (takes precedence)
    if per_page_quotas:
        for chunk_txt, page_no in chunks:
            q = int(per_page_quotas.get(int(page_no), 0))
            if q <= 0:
                continue
            q = min(q, max_cards_per_chunk)
            got = _cards_from_chunk(str(chunk_txt), page_no=int(page_no), max_cards=q)
            cards.extend(got)
            if total_cards and len(cards) >= total_cards:
                break
        return cards[: (total_cards or len(cards))]

    # 2) Global cap with fair distribution
    if total_cards is not None:
        quotas = _distribute_quota(int(total_cards), len(chunks))
        for (chunk_txt, page_no), q in zip(chunks, quotas):
            if q <= 0:
                continue
            q = min(q, max_cards_per_chunk)
            got = _cards_from_chunk(str(chunk_txt), page_no=int(page_no), max_cards=q)
            cards.extend(got)
            if len(cards) >= total_cards:
                break
        return cards[:total_cards]

    # 3) Legacy: per-chunk only
    for chunk_txt, page_no in chunks:
        got = _cards_from_chunk(str(chunk_txt), page_no=int(page_no), max_cards=max_cards_per_chunk)
        cards.extend(got)
    return cards

def write_json_for_document(
    path: pathlib.Path,
    *,
    max_tokens: int = 500,
    total_cards: int | None = None,         # allow global cap in CLI, too
    max_cards_per_chunk: int = 30,
    sample_chunks: int | None = None,
) -> pathlib.Path:
    """Generate cards, write <file>.cards.json next to the document, return the Path."""
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
