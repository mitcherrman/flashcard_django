# flashcards/ai/pipeline/templater.py
from __future__ import annotations
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from openai import OpenAI

# Prefer TOC-aware section chunks; falls back to per-page
from ..driver import run_extraction

log = logging.getLogger(__name__)

# --------------------------------------------------------------------
# Tuning knobs you requested
# --------------------------------------------------------------------
MAX_CHARS_SINGLE = 24000         # if the whole doc fits, do 1 LLM call
MAX_TOKENS_CHUNK = 600           # when we (re)chunk with driver, cap per chunk

# --------------------------------------------------------------------
# Utilities
# --------------------------------------------------------------------
def _norm(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[\s\u200b]+", " ", s)
    s = re.sub(r"[^\w\s&:+/().,'’\-^*=\[\]{}|]", "", s)
    return s.strip()

def _concat(chunks: List[Tuple[str, int]]) -> Tuple[str, int]:
    """
    Concatenate chunk texts and determine an approximate pages count
    from the max page number observed.
    """
    big = []
    max_page = 1
    for txt, pg in chunks:
        big.append(str(txt or ""))
        big.append("\n\n")
        try:
            max_page = max(max_page, int(pg))
        except Exception:
            pass
    return "".join(big), max_page

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
# LLM call – we keep your simple intent, but request strict JSON so parsing is reliable
# --------------------------------------------------------------------
_SYSTEM = (
    "You are a study-note generator. Read the source text and produce incredibly detailed notes "
    "organized into sections. For each section, write exactly 5–6 bullet points. "
    "Each bullet must be a question followed by a colon and a concise answer on the same line."
)

# We ask the model to emit JSON we can parse deterministically.
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

    client = OpenAI()
    msg_user = _USER_TEMPLATE.format(header_hint=header_hint, body=text)

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": msg_user},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=1400,
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
# Public API – build the template (LLM-first; TOC-aware when long)
# --------------------------------------------------------------------
def build_template_from_chunks(
    chunks: List[Tuple[str, int]],
    *,
    title: str = "Untitled",
    path: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Returns:
      {
        "version": "study-template/llm-v1",
        "title": <title>,
        "pages": <pages>,
        "sections": [
          {
            "title": "...",
            "page_start": int|null,
            "page_end": int|null,
            "items": [
              {
                "type": "concept",
                "term": <question>,
                "definition": <answer>,
                "source_excerpt": "<Q: A>",
                "page": int|null,
                "ordinal": int
              }, ...
            ]
          }, ...
        ],
        "toc": [{"title":"...", "page_start":..., "page_end":..., "ordinal_first": ...}, ...]
      }
    """
    # 1) Concatenate what we already have (from core/driver) and quick size check
    doc_text, pages_count = _concat(chunks)

    # 2) Short doc → single LLM call
    if len(doc_text) <= MAX_CHARS_SINGLE:
        secs = _ask_llm_sections(doc_text, title_hint=title)
        return _template_from_sections(secs, pages=pages_count, title=title)

    # 3) Long doc → prefer TOC-aware chunks via driver; one LLM call per section/page chunk.
    #    NOTE: run_extraction() will itself fall back to per-page if no TOC.
    try:
        extracted = run_extraction(path, max_tokens=MAX_TOKENS_CHUNK) if path else []
    except Exception as e:
        log.warning("run_extraction failed; falling back to single-call best-effort: %s", e)
        secs = _ask_llm_sections(doc_text, title_hint=title)
        return _template_from_sections(secs, pages=pages_count, title=title)

    if not extracted:
        # Last resort: still try single call on the first 24k chars
        secs = _ask_llm_sections(doc_text[:MAX_CHARS_SINGLE], title_hint=title)
        return _template_from_sections(secs, pages=pages_count, title=title)

    # extracted: list of (text, page_start, section_title|None)
    merged: List[Section] = []
    for (chunk_text, page_start, sec_title) in extracted:
        secs = _ask_llm_sections(
            chunk_text,
            title_hint=title,
            section_hint=sec_title if isinstance(sec_title, str) and sec_title.strip() else None,
        )
        # attach page metadata to sections
        for s in secs:
            s.page_start = int(page_start) if page_start is not None else None
            s.page_end = None
        merged = _merge_sections(merged, secs)

    return _template_from_sections(merged, pages=pages_count, title=title)

# --------------------------------------------------------------------
# Convert LLM Sections → your template schema (items are QA pairs)
# --------------------------------------------------------------------
def _template_from_sections(sections: List[Section], *, pages: int, title: str) -> Dict[str, Any]:
    ordinal = 1
    out_sections: List[Dict[str, Any]] = []
    toc: List[Dict[str, Any]] = []

    for s in sections:
        items: List[Dict[str, Any]] = []
        first_ord = ordinal
        for b in s.bullets:
            # Represent Q/A as a "concept" item with term/definition,
            # so your existing _text_for_item() yields "Q: A" nicely.
            items.append({
                "type": "concept",
                "term": b.q,
                "definition": b.a,
                "source_excerpt": f"{b.q}: {b.a}",
                "page": s.page_start or None,
                "ordinal": ordinal,
            })
            ordinal += 1

        out_sections.append({
            "title": s.title or "Section",
            "page_start": s.page_start or None,
            "page_end": s.page_end or None,
            "items": items,
        })

        toc.append({
            "title": s.title or "Section",
            "page_start": s.page_start or None,
            "page_end": s.page_end or None,
            "ordinal_first": first_ord,
        })

    return {
        "version": "study-template/llm-v1",
        "title": title,
        "pages": int(pages or 1),
        "sections": out_sections,
        "toc": toc,
    }
