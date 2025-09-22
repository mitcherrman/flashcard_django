"""
flashcards.ai.pipeline.core
———————————
Pure-library helpers – **NO Django, NO prints**.

• cards_from_document(path, …) → list[dict]   (used by views.generate_deck)
• write_json_for_document(path, …) → Path     (optional utility)
"""
from __future__ import annotations
import pathlib, random, pickle, json, logging, re
from typing import List, Tuple, Dict, Optional, Any
from collections import defaultdict

from ..driver         import run_extraction
from ..flashcard_gen  import _cards_from_chunk, build_card_key
from .templater       import build_template_from_chunks

log = logging.getLogger(__name__)

# ───────────────────────── helpers ─────────────────────────

def _normalize_chunks(raw_chunks) -> List[Tuple[str, int]]:
    """
    Convert driver output into [(text, page)] in natural order.
    Accepts tuples/lists/dicts/strings from the driver.
    """
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

    norm: List[Tuple[str, int]] = []
    if not raw_chunks:
        return norm

    for idx, item in enumerate(raw_chunks, start=1):
        if isinstance(item, (tuple, list)) and item:
            txt = item[0]
            page = _pick_page_from(list(item[1:]), fallback=idx)
            norm.append((str(txt), page)); continue
        if isinstance(item, dict):
            txt = item.get("text", "") or ""
            page = item.get("page") or item.get("page_start") or idx
            norm.append((str(txt), int(page))); continue
        norm.append((str(item), idx))
    return norm


def _distribute_quota(total: int, parts: int) -> list[int]:
    if parts <= 0:
        return []
    base, extra = divmod(max(0, int(total)), parts)
    return [base + (1 if i < extra else 0) for i in range(parts)]


def _norm(s: str) -> str:
    """Loose normalization for fuzzy title matching."""
    s = (s or "").lower()
    s = re.sub(r"[\s\u200b]+", " ", s)
    s = re.sub(r"[^\w\s&:+/().,'’\-^*=\[\]{}|]", "", s)
    return s.strip()


# ───────────────── item → short text payload ─────────────────

def _text_for_item(item: dict, section_title: str) -> str:
    t = (item.get("type") or "").lower()
    ex = item.get("source_excerpt") or ""
    if t == "definition":
        term = item.get("term") or ""
        definition = item.get("definition") or ""
        if term and definition:
            return f"{term}: {definition}"
        return ex or definition or term
    if t == "formula":
        name = item.get("name") or "Formula"
        expr = item.get("expression") or ""
        return f"{name}: {expr}" if expr else (ex or name)
    if t == "example":
        prompt = item.get("prompt") or ""
        sol = item.get("solution") or ""
        return f"Example: {prompt}" + (f" = {sol}" if sol else "")
    if t == "concept":
        term = item.get("term") or ""
        definition = item.get("definition") or ""
        if term and definition:
            return f"{term}: {definition}"
        return definition or term or ex
    # fallback
    return ex or f"{section_title} – key point"


# ───────────────────── public API ──────────────────────────

def cards_from_document(
    path: pathlib.Path,
    *,
    # global control
    total_cards: int | None = None,           # optional overall cap (still respects per-section caps)
    max_cards_per_section: int = 8,           # NEW default hard cap per section
    # extraction / testing
    max_tokens: int = 500,
    sample_chunks: int | None = None,
    cache_chunks: bool = True,
    # UI plan (section → requested #cards). Titles must match user UI.
    sections_plan: Optional[List[Dict[str, Any]]] = None,  # [{title,page_start,page_end,cards}]
) -> List[dict]:
    """
    Turn a document into list[card-dict] using the Study Template.

    Behavior:
      • Build a template (heading→next heading; items inside).
      • For each section, try to generate as many *unique* cards as possible
        up to min(requested, max_cards_per_section). If no plan is provided:
          – if total_cards is set: distribute across sections and cap per section
          – else: aim for max_cards_per_section for every section
      • Output is in *document order* (by item ordinal; then page; then sequence).
    """
    raw = run_extraction(path, max_tokens=max_tokens)
    chunks = _normalize_chunks(raw)  # -> [(text, page)]
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

    # Build study template from the extracted chunks
    template = build_template_from_chunks(chunks, title=path.stem if hasattr(path, "stem") else "Document")
    sections: List[dict] = template.get("sections", [])

    # Determine per-section targets
    targets: Dict[str, int] = {}

    if sections_plan:
        # Use UI plan but cap at max_cards_per_section
        for a in sections_plan:
            title = a.get("title") or ""
            req = max(0, int(a.get("cards") or 0))
            targets[_norm(title)] = min(req, max_cards_per_section)
    elif total_cards is not None and sections:
        quotas = _distribute_quota(int(total_cards), len(sections))
        for sec, q in zip(sections, quotas):
            targets[_norm(sec.get("title", ""))] = min(int(q), max_cards_per_section)
    else:
        # No plan, no global total → use per-section cap
        for sec in sections:
            targets[_norm(sec.get("title",""))] = max_cards_per_section

    # Prepare generation state
    cards: list[dict] = []
    seen_keys: set[str] = set()
    gen_seq = 0  # stable tie-breaker in sort

    def _keep(c: dict) -> bool:
        k = build_card_key(c.get("front",""), c.get("back",""))
        if not k or k in seen_keys:
            return False
        seen_keys.add(k)
        c["card_key"] = k
        return True

    # Iterate sections in document order
    for sec in sections:
        sec_title = sec.get("title") or ""
        sec_key = _norm(sec_title)
        target = int(targets.get(sec_key, 0))
        if target <= 0:
            continue

        items: List[dict] = sec.get("items") or []
        if not items:
            continue

        kept_for_section = 0
        idx_item = 0
        passes_without_new = 0

        # Cycle items until we reach target or make a full unproductive pass
        while kept_for_section < target and passes_without_new < 2:
            started = kept_for_section
            # One pass over all items in-order
            for i in range(len(items)):
                if kept_for_section >= target:
                    break
                item = items[(idx_item + i) % len(items)]
                text = _text_for_item(item, sec_title)
                page_no = int(item.get("page") or sec.get("page_start") or 1)

                # Ask model for 1 card per item (encourages variety + dedupe)
                out = _cards_from_chunk(
                    text,
                    page_no=page_no,
                    section=sec_title,
                    max_cards=1
                )
                for c in out:
                    # Attach metadata for order
                    c["section"] = sec_title
                    c["page"] = page_no if isinstance(c.get("page"), int) else page_no
                    c["ordinal"] = int(item.get("ordinal") or 10**9)  # doc-order anchor
                    c["_gen_seq"] = gen_seq
                    gen_seq += 1
                    if _keep(c):
                        cards.append(c)
                        kept_for_section += 1
                        if kept_for_section >= target:
                            break

            if kept_for_section == started:
                passes_without_new += 1
            else:
                passes_without_new = 0
            idx_item = (idx_item + 1) % len(items)

        # move to next section when done (we do not warn; we simply keep what we have)

    # Optional global trim if total_cards is set
    if total_cards is not None and len(cards) > int(total_cards):
        cards = cards[: int(total_cards)]

    # Stable document order
    cards.sort(key=lambda c: (int(c.get("ordinal") or 10**8), int(c.get("page") or 10**6), int(c.get("_gen_seq") or 0)))

    # Clean internal fields
    for c in cards:
        c.pop("_gen_seq", None)

    return cards


def write_json_for_document(
    path: pathlib.Path,
    *,
    max_tokens: int = 500,
    total_cards: int | None = None,
    max_cards_per_section: int = 8,
    sample_chunks: int | None = None,
) -> pathlib.Path:
    cards = cards_from_document(
        path,
        max_tokens=max_tokens,
        total_cards=total_cards,
        max_cards_per_section=max_cards_per_section,
        sample_chunks=sample_chunks,
        cache_chunks=False,
    )
    out = path.with_suffix(".cards.json")
    out.write_text(json.dumps(cards, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("Card JSON written → %s (%s cards)", out.name, len(cards))
    return out
