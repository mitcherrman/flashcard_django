# flashcards/ai/pipeline/core.py
"""
flashcards.ai.pipeline.core
———————————
Pure-library helpers – **NO Django, NO prints**.

• cards_from_document(path, …) → list[dict]   (for views.generate_deck)
• write_json_for_document(path, …) → Path     (optional utility)
"""
from __future__ import annotations
import pathlib, random, pickle, json, logging, re
from typing import List, Tuple, Dict, Optional, Any
from collections import defaultdict

from ..driver         import run_extraction
from ..flashcard_gen  import _cards_from_chunk, build_card_key

log = logging.getLogger(__name__)

# ───────────────────────── helpers ─────────────────────────
def _pick_page_from(item_parts: List[Any], fallback: int) -> int:
    page: Optional[int] = None
    for elem in item_parts:
        if isinstance(elem, int):
            page = elem; break
        if isinstance(elem, (tuple, list)) and len(elem) == 2 and all(isinstance(x, int) for x in elem):
            page = elem[0]; break
        if isinstance(elem, dict):
            if "page" in elem and isinstance(elem["page"], int):
                page = elem["page"]; break
            if "page_start" in elem and isinstance(elem["page_start"], int):
                page = elem["page_start"]; break
    return int(page if page is not None else fallback)

def _normalize_chunks(raw_chunks) -> List[Tuple[str, int]]:
    norm: List[Tuple[str, int]] = []
    if not raw_chunks:
        return norm
    for idx, item in enumerate(raw_chunks, start=1):
        if isinstance(item, (tuple, list)) and item:
            txt = item[0]
            page = _pick_page_from(list(item[1:]), fallback=idx)
            norm.append((str(txt), page)); continue
        if isinstance(item, dict):
            txt = item.get("text", "")
            page = item.get("page") or item.get("page_start") or idx
            norm.append((str(txt), int(page))); continue
        norm.append((str(item), idx))
    return norm

def _distribute_quota(total: int, parts: int) -> list[int]:
    if parts <= 0:
        return []
    base, extra = divmod(total, parts)
    return [base + (1 if i < extra else 0) for i in range(parts)]

# Heuristic “fact” counter (bullets, equations, definitions, short atomic sentences)
_BULLET = re.compile(r"^\s*(?:[-*•]|[0-9]+[.)])\s+")
_EQUATION = re.compile(r"[=≈≃≥≤±∝^]")
_DEF = re.compile(r"\b(is defined as|means|refers to|defined as|:)\b", re.I)

def _estimate_facts(text: str) -> int:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    score = 0
    for ln in lines:
        if _BULLET.search(ln):
            score += 1; continue
        if _EQUATION.search(ln):
            score += 1; continue
        if _DEF.search(ln):
            score += 1; continue
        # compact “atomic” sentence
        if ln.endswith("."):
            tokens = ln.split()
            if 6 <= len(tokens) <= 24:
                score += 1
    return max(1, score)  # always allow at least 1

# ───────────────────── public API ──────────────────────────
def cards_from_document(
    path: pathlib.Path,
    *,
    # global control
    total_cards: int | None = None,
    max_cards_per_chunk: int = 30,
    # extraction / testing
    max_tokens: int = 500,
    sample_chunks: int | None = None,
    cache_chunks: bool = True,
    # quotas & sections
    per_page_section_quotas: Optional[Dict[int, List[tuple[str,int]]]] = None,  # NEW
    per_page_quotas: Optional[Dict[int, int]] = None,                            # legacy
    page_to_section: Optional[Dict[int, str]] = None,                            # legacy
    section_caps: Optional[Dict[str, int]] = None,
    autoguess_section_caps: bool = True,
) -> List[dict]:
    raw = run_extraction(path, max_tokens=max_tokens)
    chunks = _normalize_chunks(raw)                      # -> [(text, page)]
    log.info("core: %s chunk(s) ready", len(chunks))

    if sample_chunks:
        k = min(sample_chunks, len(chunks))
        chunks = random.sample(chunks, k)

    if cache_chunks:
        cache = path.with_suffix(".chunks.pkl")
        try:
            cache.write_bytes(pickle.dumps(chunks))
        except Exception as e:
            log.warning("Could not write chunk cache %s: %s", cache, e)

    # combine text per page (if multiple chunks per page, join them)
    from collections import defaultdict
    page_texts: Dict[int, List[str]] = defaultdict(list)
    for txt, pg in chunks:
        page_texts[int(pg)].append(str(txt))
    page_blob: Dict[int, str] = {pg: "\n\n".join(parts) for pg, parts in page_texts.items()}

    # Build section text (for optional auto caps)
    section_text: Dict[str, list[str]] = defaultdict(list)
    if per_page_section_quotas:
        for pg, lst in per_page_section_quotas.items():
            if pg in page_blob:
                for sec, _ in lst:
                    section_text[sec].append(page_blob[pg])
    elif page_to_section:
        for pg, sec in page_to_section.items():
            if sec and pg in page_blob:
                section_text[sec].append(page_blob[pg])

    if section_caps is None and autoguess_section_caps and section_text:
        section_caps = {sec: _estimate_facts("\n".join(txts)) for sec, txts in section_text.items()}

    cards: list[dict] = []
    seen_keys: set[str] = set()
    used_per_section: Dict[str, int] = defaultdict(int)

    def _section_allowed(sec: Optional[str]) -> bool:
        if not sec or not section_caps:
            return True
        cap = section_caps.get(sec)
        return (cap is None) or (used_per_section[sec] < cap)

    def _keep_card(c: dict) -> bool:
        k = build_card_key(c.get("front", ""), c.get("back", ""))
        if not k or k in seen_keys:
            return False
        sec = c.get("section")
        if not _section_allowed(sec):
            return False
        seen_keys.add(k)
        if sec:
            used_per_section[sec] += 1
        c["card_key"] = k
        return True

    # ── 1) NEW: per-page, per-section quotas (preserve page order and UI section order)
    if per_page_section_quotas:
        for page_no in sorted(per_page_section_quotas.keys()):
            chunk_txt = page_blob.get(int(page_no))
            if not chunk_txt:
                continue
            for section, need in per_page_section_quotas[page_no]:
                need = min(int(need), max_cards_per_chunk)
                while need > 0:
                    # obey section caps if present
                    if section and section_caps and section in section_caps:
                        remaining = max(0, section_caps[section] - used_per_section[section])
                        if remaining <= 0:
                            break
                        batch = min(3, need, remaining)
                    else:
                        batch = min(3, need)

                    got = _cards_from_chunk(chunk_txt, page_no=int(page_no), section=section, max_cards=batch)
                    added = 0
                    for c in got:
                        c.setdefault("page", int(page_no))
                        c.setdefault("section", section)
                        if _keep_card(c):
                            cards.append(c)
                            added += 1

                    if added == 0:
                        break
                    need -= added

                if total_cards and len(cards) >= total_cards:
                    break
        return cards[: (total_cards or len(cards))]

    # ── 2) Legacy: per-page quotas (single section per page)
    def _section_for_page(pg: int) -> Optional[str]:
        return page_to_section.get(int(pg)) if page_to_section else None

    if per_page_quotas:
        for chunk_txt, page_no in chunks:
            q = int(per_page_quotas.get(int(page_no), 0))
            if q <= 0:
                continue
            q = min(q, max_cards_per_chunk)
            sec = _section_for_page(page_no)
            while q > 0:
                if sec and section_caps and sec in section_caps:
                    remaining = max(0, section_caps[sec] - used_per_section[sec])
                    if remaining <= 0:
                        break
                    batch = min(3, q, remaining)
                else:
                    batch = min(3, q)
                got = _cards_from_chunk(str(chunk_txt), page_no=int(page_no), section=sec, max_cards=batch)
                added = 0
                for c in got:
                    if _keep_card(c):
                        cards.append(c); added += 1
                if added == 0:
                    break
                q -= added
            if total_cards and len(cards) >= total_cards:
                break
        return cards[: (total_cards or len(cards))]

    # ── 3) Global cap distribution across chunks
    if total_cards is not None:
        quotas = _distribute_quota(int(total_cards), len(chunks))
        for (chunk_txt, page_no), q in zip(chunks, quotas):
            if q <= 0:
                continue
            q = min(q, max_cards_per_chunk)
            sec = _section_for_page(page_no)
            while q > 0:
                batch = min(3, q)
                got = _cards_from_chunk(str(chunk_txt), page_no=int(page_no), section=sec, max_cards=batch)
                added = 0
                for c in got:
                    if _keep_card(c):
                        cards.append(c); added += 1
                if added == 0:
                    break
                q -= added
            if len(cards) >= total_cards:
                break
        return cards[:total_cards]

    # ── 4) Legacy per chunk (no global cap)
    for chunk_txt, page_no in chunks:
        sec = _section_for_page(page_no)
        got = _cards_from_chunk(str(chunk_txt), page_no=int(page_no), section=sec, max_cards=max_cards_per_chunk)
        for c in got:
            if _keep_card(c):
                cards.append(c)
    return cards


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
