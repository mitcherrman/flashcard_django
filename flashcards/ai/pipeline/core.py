from __future__ import annotations
import pathlib, random, pickle, json, logging
from typing import List, Tuple, Optional
from ..driver         import run_extraction
from ..flashcard_gen  import _cards_from_chunk

log = logging.getLogger(__name__)

def _normalize_chunks(raw_chunks) -> List[Tuple[str, int, Optional[str]]]:
    """
    Accept:
      • [str, ...]                          → (text, page=1..N, section=None)
      • [(text, page)]                      → (text, page, section=None)
      • [(text, page, section)]             → (text, page, section)
    """
    norm: List[Tuple[str, int, Optional[str]]] = []
    if not raw_chunks:
        return norm
    first = raw_chunks[0]
    if isinstance(first, tuple):
        if len(first) == 3:
            for txt, p, sec in raw_chunks:
                norm.append((txt, int(p), sec))
        elif len(first) == 2:
            for txt, p in raw_chunks:
                norm.append((txt, int(p), None))
        else:
            # unexpected shape; coerce to strings with implicit pages
            for i, txt in enumerate(raw_chunks, start=1):
                norm.append((str(txt), i, None))
    else:
        for i, txt in enumerate(raw_chunks, start=1):
            norm.append((str(txt), i, None))
    return norm

def _distribute_quota(total: int, parts: int) -> list[int]:
    if parts <= 0:
        return []
    base, extra = divmod(total, parts)
    return [base + (1 if i < extra else 0) for i in range(parts)]

def cards_from_document(
    path: pathlib.Path,
    *,
    total_cards: int | None = None,
    max_cards_per_chunk: int = 30,     # allow >3 so sections can get multiple cards
    max_tokens: int = 500,
    sample_chunks: int | None = None,
    cache_chunks: bool = True,
) -> List[dict]:
    raw = run_extraction(path, max_tokens=max_tokens)
    chunks = _normalize_chunks(raw)                       # [(text, page, section?)]
    log.info("core: %s chunk(s) ready", len(chunks))

    if sample_chunks:
        k = min(sample_chunks, len(chunks))
        chunks = random.sample(chunks, k)
        log.debug("Sampled %s random chunk(s) for test/demo", len(chunks))

    if cache_chunks:
        cache = path.with_suffix(".chunks.pkl")
        try:
            cache.write_bytes(pickle.dumps(chunks))
            log.debug("Chunk cache written → %s", cache.name)
        except Exception as e:
            log.warning("Could not write chunk cache %s: %s", cache, e)

    cards: list[dict] = []

    if total_cards is None:
        # Per-chunk behavior without a global cap
        for chunk_txt, page_no, section in chunks:
            got = _cards_from_chunk(
                chunk_txt, page_no=page_no, section=section, max_cards=max_cards_per_chunk
            )
            cards.extend(got)
        return cards

    # Global target distributed evenly across *sections* (chunks)
    quotas = _distribute_quota(total_cards, len(chunks))
    for (chunk_txt, page_no, section), q in zip(chunks, quotas):
        if q <= 0:
            continue
        q = min(q, max_cards_per_chunk)
        got = _cards_from_chunk(chunk_txt, page_no=page_no, section=section, max_cards=q)
        cards.extend(got)
        if len(cards) >= total_cards:
            break

    return cards[:total_cards]

def write_json_for_document(
    path: pathlib.Path,
    *,
    max_tokens: int = 500,
    total_cards: int | None = None,
    max_cards_per_chunk: int = 30,
    sample_chunks: int | None = None,
) -> pathlib.Path:
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
