from __future__ import annotations
import pathlib, random, pickle, json, logging, re
from typing import List, Tuple, Dict, Optional, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..driver         import run_extraction, page_texts
from ..flashcard_gen  import cards_from_chunk, build_card_key
from .templater       import build_template_from_chunks

log = logging.getLogger(__name__)

MAX_CHARS_SINGLE: int = 24_000
DEFAULT_MAX_TOKENS: int = 600
DEFAULT_CONCURRENCY: int = 4

def _normalize_chunks(raw_chunks) -> List[Tuple[str, int]]:
    # (unchanged)
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
    s = (s or "").lower()
    s = re.sub(r"[\s\u200b]+", " ", s)
    s = re.sub(r"[^\w\s&:+/().,'’\-^*=\[\]{}|]", "", s)
    return s.strip()

def _chunk_spans(raw_chunks, chunks: List[Tuple[str, int]]) -> List[Tuple[str, int, int, Optional[str]]]:
    """(text, page_start, page_end, title|None) for each normalized chunk."""
    spans = []
    for item, (txt, ps) in zip(raw_chunks or [], chunks):
        title, pe = None, ps
        if isinstance(item, (tuple, list)) and len(item) >= 4:
            title = item[2] if isinstance(item[2], str) and item[2].strip() else None
            pe = item[3] if isinstance(item[3], int) else ps
        spans.append((txt, ps, max(ps, pe), title))
    return spans

def _real_range(sec: dict) -> Optional[Tuple[int, int]]:
    """The section's page range, or None when it is only an estimate."""
    ps, pe = sec.get("page_start"), sec.get("page_end")
    if sec.get("page_source") == "estimated":
        return None
    if isinstance(ps, int) and isinstance(pe, int) and ps <= pe:
        return ps, pe
    return None

def _section_text_from_pages(sec: dict, spans, max_chars: int = MAX_CHARS_SINGLE) -> str:
    rng = _real_range(sec)
    if rng is None:
        return ""  # no real range: use seed items only, never a guessed page slice
    ps, pe = rng
    # Prefer the section's own chunk (TOC entries can share a start page),
    # otherwise every chunk lying wholly inside the section's range.
    key = _norm(sec.get("title") or "")
    picked = [s for s in spans if s[3] and _norm(s[3]) == key and ps <= s[1] <= pe]
    if not picked:
        picked = [s for s in spans if ps <= s[1] and s[2] <= pe]
    parts: List[str] = []
    for txt, *_ in picked:
        t = (txt or "").strip()
        if t:
            parts.append(t)
    joined = "\n\n".join(parts).strip()
    return joined[:max_chars] if len(joined) > max_chars else joined

_WORD_RE = re.compile(r"\w+")

def _words(s: str) -> List[str]:
    return _WORD_RE.findall((s or "").lower())

def _locate_excerpt(excerpt: str, norm_pages: List[str], lo: int, hi: int) -> Optional[int]:
    """First page in [lo, hi] whose text contains the card's excerpt (or its
    first/last 8 words) verbatim, ignoring case/punctuation/whitespace."""
    words = _words(excerpt)
    if len(words) < 4:
        return None
    needles = [words] if len(words) <= 8 else [words[:8], words[-8:]]
    for needle in needles:
        pat = " " + " ".join(needle) + " "
        for p in range(max(1, lo), min(hi, len(norm_pages)) + 1):
            if pat in norm_pages[p - 1]:
                return p
    return None

def _grounded_page(card: dict, sec: Optional[dict], norm_pages: List[str]) -> Optional[int]:
    """
    Card page policy (the model's echoed page is never used):
      1. the page within the section's real range where the excerpt is found;
      2. else the section's only page, if its real range is a single page;
      3. else None — a multi-page section does not imply a page;
      4. without a real range, only an excerpt located verbatim anywhere in
         the document grounds a page; else None.
    """
    rng = _real_range(sec) if sec else None
    lo, hi = rng if rng else (1, len(norm_pages))
    found = _locate_excerpt(card.get("excerpt") or "", norm_pages, lo, hi)
    if found is not None:
        return found
    if rng and rng[0] == rng[1]:
        return rng[0]
    return None

def _fallback_text_from_items(sec: dict, max_chars: int = MAX_CHARS_SINGLE) -> str:
    out: List[str] = []
    for it in (sec.get("items") or []):
        q = (it.get("term") or it.get("q") or "").strip()
        a = (it.get("definition") or it.get("a") or "").strip()
        line = (it.get("source_excerpt") or "").strip()
        if line:
            out.append(line)
        elif q and a:
            out.append(f"{q}: {a}")
    blob = "\n".join(out).strip()
    return blob[:max_chars] if len(blob) > max_chars else blob

def _mix_text(page_text: str, seed_lines: str, limit: int = MAX_CHARS_SINGLE) -> str:
    """Combine page slice + seed QA so each section input is distinctive."""
    parts = []
    if page_text:
        parts.append(page_text)
    if seed_lines:
        parts.append("\n\nSEED QA LINES:\n" + seed_lines)
    if not parts:
        return ""
    blob = ("\n".join(parts)).strip()
    return blob[:limit] if len(blob) > limit else blob

def cards_from_document(
    path: pathlib.Path,
    *,
    total_cards: int | None = None,
    max_cards_per_section: int = 8,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    sample_chunks: int | None = None,
    cache_chunks: bool = True,
    concurrency: int = DEFAULT_CONCURRENCY,
    sections_plan: Optional[List[Dict[str, Any]]] = None,
    return_template: bool = False,   # ← NEW
    title: Optional[str] = None,
):
    raw = run_extraction(path, max_tokens=max_tokens)
    chunks = _normalize_chunks(raw)
    spans = _chunk_spans(raw, chunks)
    log.info("core: %s chunk(s) ready", len(chunks))

    if sample_chunks:
        k = min(sample_chunks, len(spans))
        spans = random.sample(spans, k)
        chunks = [(t, ps) for t, ps, *_ in spans]
        log.debug("Sampled %s random chunk(s) for test/demo", len(chunks))

    try:
        norm_pages = [" " + " ".join(_words(t)) + " " for t in page_texts(path)]
    except Exception as e:
        log.warning("core: could not read page text for grounding: %s", e)
        norm_pages = []

    if cache_chunks:
        cache = path.with_suffix(".chunks.pkl")
        try:
            cache.write_bytes(pickle.dumps(chunks))
            log.debug("Chunk cache written → %s", cache.name)
        except Exception as e:
            log.warning("Could not write chunk cache %s: %s", cache, e)

    # Build the LLM study template once here
    template = build_template_from_chunks(
        chunks,
        title=title or (path.stem if hasattr(path, "stem") else "Document"),
        path=path,
    )
    sections: List[dict] = template.get("sections", []) or []
    if not sections:
        return ([], template) if return_template else []

    # targets
    targets: Dict[str, int] = {}
    if sections_plan:
        for a in sections_plan:
            title = a.get("title") or ""
            req = max(0, int(a.get("cards") or 0))
            targets[_norm(title)] = min(req, max_cards_per_section)
    elif total_cards is not None and sections:
        quotas = _distribute_quota(int(total_cards), len(sections))
        for sec, q in zip(sections, quotas):
            targets[_norm(sec.get("title", ""))] = min(int(q), max_cards_per_section)
    else:
        for sec in sections:
            targets[_norm(sec.get("title",""))] = max_cards_per_section

    # worker
    def _gen_for_section(sec_index: int, sec: dict, target: int) -> tuple[int, list[dict]]:
        title = sec.get("title") or ""
        rng = _real_range(sec)
        page_start = rng[0] if rng else None  # no PAGE hint when the range is a guess

        page_text = _section_text_from_pages(sec, spans, MAX_CHARS_SINGLE)
        seed = _fallback_text_from_items(sec, MAX_CHARS_SINGLE)
        text = _mix_text(page_text, seed, MAX_CHARS_SINGLE)
        if not text:
            text = title  # last resort

        out = cards_from_chunk(text, page_no=page_start, section=title, max_cards=target)

        # top-up once if short
        if isinstance(out, list) and len(out) < target and target > 0:
            need = target - len(out)
            more = cards_from_chunk(text, page_no=page_start, section=title, max_cards=need)
            if isinstance(more, list) and more:
                out.extend(more[:need])

        return (sec_index, out or [])

    # submit jobs where target > 0
    jobs = [(i, s, int(targets.get(_norm(s.get("title") or ""), 0))) for i, s in enumerate(sections)]
    jobs = [(i, s, t) for (i, s, t) in jobs if t > 0]

    results_by_index: dict[int, list[dict]] = {}
    if jobs:
        max_workers = max(1, min(concurrency, len(jobs)))
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = [ex.submit(_gen_for_section, i, s, t) for (i, s, t) in jobs]
            for fut in as_completed(futs):
                try:
                    idx, out_cards = fut.result()
                    results_by_index[idx] = out_cards or []
                except Exception as e:
                    log.warning("core: section worker failed: %s", e)
    else:
        return ([], template) if return_template else []

    # dedupe & order
    cards: list[dict] = []
    seen_keys: set[str] = set()
    for sec_index in sorted(results_by_index.keys()):
        out_cards = results_by_index[sec_index]
        base_ord = sec_index * 10_000
        kept = 0
        for j, c in enumerate(out_cards):
            front = (c.get("front") or "").strip()
            back  = (c.get("back")  or "").strip()
            if not front or not back:
                continue
            k = c.get("card_key") or build_card_key(front, back)
            if not k or k in seen_keys:
                continue
            seen_keys.add(k)
            c["card_key"] = k
            sec = sections[sec_index]
            # Provenance is the section job the card came from, not the model's echo.
            c["section"] = sec.get("title") or c.get("section") or ""
            c["page"] = _grounded_page(c, sec, norm_pages)
            c["ordinal"] = base_ord + kept
            cards.append(c)
            kept += 1

    # global catch-up if we undershot
    if total_cards is not None and len(cards) < int(total_cards):
        need = int(total_cards) - len(cards)
        lines = []
        seen_line = set()
        for sec in sections:
            blob = _fallback_text_from_items(sec, 2000)
            for ln in blob.splitlines():
                ln = ln.strip()
                if ln and ln.lower() not in seen_line:
                    seen_line.add(ln.lower())
                    lines.append(ln)
        seed_mixed = "\n".join(lines[:800])
        mix_text = _mix_text("", seed_mixed, MAX_CHARS_SINGLE)
        if mix_text:
            try:
                extra = cards_from_chunk(mix_text, page_no=None, section="Mixed topics", max_cards=need)
            except Exception as e:
                # Like a failed section job: keep the cards already made.
                log.warning("core: catch-up failed: %s", e)
                extra = []
            for c in extra or []:
                front = (c.get("front") or "").strip()
                back  = (c.get("back")  or "").strip()
                if not front or not back:
                    continue
                k = c.get("card_key") or build_card_key(front, back)
                if not k or k in seen_keys:
                    continue
                seen_keys.add(k)
                c["card_key"] = k
                c["section"] = "Mixed topics"
                c["page"] = _grounded_page(c, None, norm_pages)  # unknown → None, not page 1
                c["ordinal"] = 9_000_000 + len(cards)
                cards.append(c)
                if len(cards) >= int(total_cards):
                    break

    if total_cards is not None and len(cards) > int(total_cards):
        cards = cards[: int(total_cards)]

    # ← NEW: return template when asked
    if return_template:
        return cards, template
    return cards
