# flashcards/ai/pipeline/templater.py
from __future__ import annotations
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from openai import OpenAI

log = logging.getLogger(__name__)

# Reuse a single OpenAI client per worker for lower overhead
CLIENT_TEMPLATER = OpenAI()

# --------------------------------------------------------------------
# Tuning knobs
# --------------------------------------------------------------------
MAX_CHARS_SINGLE = 24000         # if the whole doc fits, do 1 LLM call
MAX_TOKENS_CHUNK = 600           # retained for reference; not used directly here

# --------------------------------------------------------------------
# Utilities
# --------------------------------------------------------------------
def _norm(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[\s\u200b]+", " ", s)
    s = re.sub(r"[^\w\s&:+/().,'’\-^*=\[\]{}|]", "", s)
    return s.strip()

def _concat(chunks: List[Tuple]) -> Tuple[str, int]:
    """
    Concatenate chunk texts and determine an approximate pages count
    from the max page number observed. Each chunk is either:
      (text, page) or (text, page, title)
    """
    big: List[str] = []
    max_page = 1
    for item in chunks:
        if not item:
            continue
        # Unpack flexibly
        if len(item) >= 2:
            txt = str(item[0] or "")
            pg = item[1]
        else:
            txt = str(item[0] or "")
            pg = 1
        big.append(txt)
        big.append("\n\n")
        try:
            max_page = max(max_page, int(pg))
        except Exception:
            pass
    return "".join(big), max_page

def _extract_titles(chunks: List[Tuple]) -> List[Tuple[str, int]]:
    """Return [(title, page_start)] for chunks that include a non-empty title."""
    out: List[Tuple[str, int]] = []
    for item in chunks:
        if len(item) >= 3:
            title = (item[2] or "").strip()
            if title:
                try:
                    pg = int(item[1])
                except Exception:
                    pg = 1
                out.append((title, pg))
    return out

@dataclass
class Bullet:
    q: str
    a: str

@dataclass
class Section:
    title: str
    bullets: List[Bullet]
    page_start: Optional[int] = None
    page_end: Optional[int] = None

# --------------------------------------------------------------------
# LLM call – strict JSON so parsing is reliable
# --------------------------------------------------------------------
_SYSTEM = (
    "You are a study-note generator. Read the source text and produce incredibly detailed notes "
    "organized into sections. For each section, write exactly 5–6 bullet points. "
    "Each bullet must be a question followed by a colon and a concise answer on the same line."
)

_USER_TEMPLATE = """\
{header_hint}

RULES:
- Output JSON ONLY, no markdown, no prose.
- Schema:
{{
  "sections": [
    {{
      "title": "string",
      "bullets": [{{"q":"string","a":"string"}}, ...]   // exactly 5–6 items
    }},
    ...
  ]
}}

TEXT:
{body}
"""

def _ask_llm_sections(
    text: str,
    *,
    title_hint: Optional[str] = None,
    section_hint: Optional[str] = None,
    model: str = "gpt-4o-mini",
) -> List[Section]:
    """
    Ask the LLM for sections with 5–6 bullets (Q:A pairs), return as structured Sections.
    We *hint* title/section but let the model format bullets.
    """
    header_lines = []
    if title_hint:
        header_lines.append(f'- Document title hint: "{title_hint}"')
    if section_hint:
        header_lines.append(f'- Single-section title hint: "{section_hint}" (use this exact title)')

    header_hint = "\n".join(header_lines) if header_lines else "(no hints)"

    msg_user = _USER_TEMPLATE.format(header_hint=header_hint, body=text)

    try:
        resp = CLIENT_TEMPLATER.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": msg_user},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=1000,
        )
        raw = resp.choices[0].message.content or ""
    except Exception as e:
        log.error("LLM call failed: %s", e)
        return []

    # Robust JSON parse (allowing minimal salvage in case of wrappers)
    obj: Dict[str, Any]
    try:
        obj = json.loads(raw)
    except Exception:
        try:
            start = raw.find("{")
            end = raw.rfind("}")
            obj = json.loads(raw[start : end + 1]) if start != -1 and end != -1 else {"sections": []}
        except Exception:
            log.warning("Could not parse JSON from LLM output.")
            log.debug("templater raw first 300: %s", raw[:300])
            return []

    out: List[Section] = []
    for s in obj.get("sections", []) or []:
        title = (s.get("title") or "").strip()
        if section_hint:
            # If we asked to lock section title, enforce it
            title = section_hint
        bullets_raw = s.get("bullets") or []
        bullets: List[Bullet] = []
        for b in bullets_raw:
            q = (b.get("q") or "").strip()
            a = (b.get("a") or "").strip()
            if q and a:
                bullets.append(Bullet(q=q, a=a))
        # Force 5–6 by trimming/exactly sizing if model gives more/less
        if len(bullets) >= 5:
            bullets = bullets[:6]
            out.append(Section(title=title or "Section", bullets=bullets))
    return out

def _merge_sections(base: List[Section], extra: List[Section]) -> List[Section]:
    """
    Merge by normalized title; keep up to 6 bullets per section.
    """
    by_key: Dict[str, Section] = {}
    for s in base:
        by_key[_norm(s.title)] = s

    for s in extra:
        k = _norm(s.title)
        if k in by_key:
            existing = by_key[k]
            missing = max(0, 6 - len(existing.bullets))
            if missing > 0:
                existing.bullets.extend(s.bullets[:missing])
        else:
            by_key[k] = Section(title=s.title, bullets=s.bullets[:6])

    # Preserve original input order as best we can
    out: List[Section] = []
    seen = set()
    for s in base + extra:
        k = _norm(s.title)
        if k not in seen and k in by_key:
            out.append(by_key[k])
            seen.add(k)
    return out

# --------------------------------------------------------------------
# Convert LLM Sections → your template schema (items are QA pairs)
# --------------------------------------------------------------------
def _template_from_sections(sections: List[Section], *, pages: int, title: str) -> Dict[str, Any]:
    def _even_ranges(n: int, total_pages: int) -> List[tuple[int, int]]:
        """Divide [1..total_pages] into n contiguous 1-based ranges (balanced)."""
        total_pages = max(1, int(total_pages or 1))
        n = max(1, int(n or 1))
        out: List[tuple[int, int]] = []
        for i in range(n):
            # floor division partitioning
            start = (i * total_pages) // n + 1
            end   = ((i + 1) * total_pages) // n
            start = max(1, min(start, total_pages))
            end   = max(start, min(end, total_pages))
            out.append((start, end))
        # guarantee coverage end-to-end
        if out:
            out[0] = (1, out[0][1])
            out[-1] = (out[-1][0], total_pages)
        return out

    ordinal = 1
    out_sections: List[Dict[str, Any]] = []
    toc: List[Dict[str, Any]] = []

    # Precompute default ranges if any section lacks page metadata
    defaults = _even_ranges(len(sections), pages)

    for idx, s in enumerate(sections):
        # Fill missing page ranges with even partitions
        ps = s.page_start if isinstance(s.page_start, int) else None
        pe = s.page_end   if isinstance(s.page_end,   int) else None
        if ps is None or pe is None:
            ps, pe = defaults[idx]

        items: List[Dict[str, Any]] = []
        first_ord = ordinal
        for b in s.bullets:
            items.append({
                "type": "concept",
                "term": b.q,
                "definition": b.a,
                "source_excerpt": f"{b.q}: {b.a}",
                "page": ps,
                "ordinal": ordinal,
            })
            ordinal += 1

        out_sections.append({
            "title": s.title or "Section",
            "page_start": ps,
            "page_end": pe,
            "items": items,
        })

        toc.append({
            "title": s.title or "Section",
            "page_start": ps,
            "page_end": pe,
            "ordinal_first": first_ord,
        })

    return {
        "version": "study-template/llm-v1",
        "title": title,
        "pages": int(pages or 1),
        "sections": out_sections,
        "toc": toc,
    }

# --------------------------------------------------------------------
# Public API – build the template (LLM-first; TOC-aware fallback)
# --------------------------------------------------------------------
def build_template_from_chunks(
    chunks: List[Tuple],
    *,
    title: str = "Untitled",
    path: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Build a study template using ONLY the provided chunks (2- or 3-tuples).
      • Small docs: single LLM call on concatenated text (fast path).
      • Large docs: grouped calls (few parallel LLM calls).
      • If JSON parsing fails or returns nothing, fall back to a TOC-only template:
        sections come from the chunk titles; items are empty but still usable by the card pipeline.
    """
    doc_text, pages_count = _concat(chunks)

    # Helper: TOC-only fallback from chunk titles
    def _toc_fallback() -> Optional[Dict[str, Any]]:
        titles = _extract_titles(chunks)
        if not titles:
            return None
        sections: List[Section] = []
        n = len(titles)
        total_pages = max(1, pages_count or n)
        def_ranges: List[Tuple[int, int]] = []
        for i in range(n):
            start = (i * total_pages) // n + 1
            end   = ((i + 1) * total_pages) // n
            start = max(1, min(start, total_pages))
            end   = max(start, min(end, total_pages))
            def_ranges.append((start, end))
        for (idx, (t, p0)) in enumerate(titles):
            ps, pe = def_ranges[idx]
            s = Section(title=t, bullets=[], page_start=p0 or ps, page_end=pe)
            sections.append(s)
        return _template_from_sections(sections, pages=pages_count, title=title)

    # Prefer single-call for small docs
    if (pages_count and pages_count <= 12) or len(doc_text) <= MAX_CHARS_SINGLE:
        secs = _ask_llm_sections(doc_text, title_hint=title)
        if not secs:
            fallback = _toc_fallback()
            if fallback:
                return fallback
            # final attempt: single-call on trimmed head
            secs = _ask_llm_sections(doc_text[:MAX_CHARS_SINGLE], title_hint=title)
        return _template_from_sections(secs, pages=pages_count, title=title)

    # Large-doc grouped path (few parallel calls on balanced groups)
    groups: List[List[Tuple]] = []
    max_groups = min(6, max(2, len(chunks) // 3))
    if max_groups <= 1:
        max_groups = 2
    for i, item in enumerate(chunks):
        g = i % max_groups
        if len(groups) <= g:
            groups.append([])
        groups[g].append(item)

    from concurrent.futures import ThreadPoolExecutor, as_completed
    merged: List[Section] = []
    with ThreadPoolExecutor(max_workers=len(groups)) as ex:
        futs = [ex.submit(_ask_llm_sections, _concat(g)[0], title_hint=title) for g in groups]
        for fut in as_completed(futs):
            try:
                secs = fut.result() or []
                merged = _merge_sections(merged, secs)
            except Exception as e:
                log.warning("templater group failed: %s", e)

    if not merged:
        fallback = _toc_fallback()
        if fallback:
            return fallback
        # Last resort
        secs = _ask_llm_sections(doc_text[:MAX_CHARS_SINGLE], title_hint=title)
        return _template_from_sections(secs, pages=pages_count, title=title)

    return _template_from_sections(merged, pages=pages_count, title=title)
