# flashcards/ai/pipeline/templater.py
from __future__ import annotations
import re
from typing import List, Tuple, Dict, Any, Optional

# Reuse your existing chunk normalizer from core (pass it in directly as [(text,page)])
_HEADING_LINE = re.compile(r"^\s*(?:[A-Z][\w &/’'()\-:]+)\s*$")
_BULLET = re.compile(r"^\s*(?:•|-|\*|[0-9]+[.)])\s+")
_FORMULA_HINT = re.compile(r"[=≈≃≥≤±∝^]|d/dx|\bintegral\b|\b∫")
_EXAMPLE_HINT = re.compile(r"^\s*example\s*[:\-]", re.I)
_TERM_DEF_SPLIT = re.compile(r"^\s*([^:]+):\s*(.+)$")

def _norm(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[\s\u200b]+", " ", s)
    s = re.sub(r"[^\w\s&:+/().,'’\-^*=\[\]{}|]", "", s)
    return s.strip()

def _concat(chunks: List[Tuple[str, int]]):
    big = []
    idx = 0
    spans = []  # (start,end,page)
    for txt, pg in chunks:
        t = str(txt or "")
        big.append(t); L = len(t)
        spans.append((idx, idx+L, int(pg)))
        idx += L
        big.append("\n\n"); idx += 2
    return "".join(big), spans

def _page_at(pos: int, spans: List[Tuple[int,int,int]]) -> int:
    for s, e, p in spans:
        if s <= pos < e: return p
    return spans[0][2] if spans else 1

def _heading_positions(doc_text: str, candidate_titles: List[str]) -> Dict[str, int]:
    N = _norm(doc_text)
    pos = {}
    for t in candidate_titles:
        nt = _norm(t)
        i = N.find(nt) if nt else -1
        pos[t] = i
    return pos

def _discover_headings(doc_text: str) -> List[str]:
    # Fall back discovery if caller didn’t pass titles; we scan for stand-alone lines
    titles = []
    for line in doc_text.splitlines():
        if _HEADING_LINE.match(line.strip()):
            # Heuristic: prefer “title-ish” lines that appear more than plain text
            if len(line.split()) <= 10:  # short-ish heading
                titles.append(line.strip())
    # Dedup while preserving order
    seen = set(); out=[]
    for t in titles:
        nt=_norm(t)
        if nt and nt not in seen:
            seen.add(nt); out.append(t)
    return out

def _split_by_headings(doc_text: str, spans: List[Tuple[int,int,int]], titles: Optional[List[str]]):
    if not titles: titles = _discover_headings(doc_text)
    offs = _heading_positions(doc_text, titles)

    ordered = sorted(
        [(o if o >= 0 else 10**12, t) for t, o in offs.items()],
        key=lambda x: x[0]
    )
    slices = []
    for i, (start, title) in enumerate(ordered):
        if start >= 10**12:  # not found
            continue
        end = len(doc_text)
        if i+1 < len(ordered) and ordered[i+1][0] < 10**12:
            end = max(ordered[i+1][0], start+20)
        page = _page_at(start, spans)
        chunk = doc_text[start:end]
        slices.append({"title": title.strip(), "page_start": page, "page_end": _page_at(end-1, spans), "text": chunk})
    return slices

def _lines(text: str):
    for raw in text.splitlines():
        ln = raw.strip()
        if ln:
            yield ln

def _classify_line(ln: str) -> str:
    if _EXAMPLE_HINT.search(ln): return "example"
    if _FORMULA_HINT.search(ln): return "formula"
    if _TERM_DEF_SPLIT.match(ln): return "definition"
    # bullets often hide defs/theorems; classify later by content
    if _BULLET.match(ln): return "bullet"
    # last resort
    return "text"

def _build_items_for_section(section_text: str, start_page: int, ordinal_start: int) -> List[Dict[str,Any]]:
    items: List[Dict[str,Any]] = []
    ordinal = ordinal_start

    # Coalesce consecutive lines that belong together (examples / multi-line bullets)
    buffer = []
    def flush_buffer():
        nonlocal ordinal
        if not buffer: return
        blob = " ".join(buffer).strip()
        page = start_page  # representative; optionally refine via spans
        kind = _classify_line(buffer[0])

        if kind == "example":
            # Example: "Example: d/dx [...] = ... "
            body = re.sub(r"^\s*example\s*[:\-]\s*", "", blob, flags=re.I)
            # try split prompt → solution
            if "=" in body:
                left, right = body.split("=", 1)
                items.append({
                    "type":"example", "prompt": left.strip(), "solution": right.strip(),
                    "source_excerpt": blob[:180], "page": page, "ordinal": ordinal
                })
            else:
                items.append({
                    "type":"example", "prompt": body, "solution": "", "source_excerpt": blob[:180],
                    "page": page, "ordinal": ordinal
                })
        elif kind == "definition":
            m = _TERM_DEF_SPLIT.match(blob)
            term, definition = m.group(1).strip(), m.group(2).strip()
            items.append({
                "type":"definition", "term": term, "definition": definition,
                "source_excerpt": blob[:180], "page": page, "ordinal": ordinal
            })
        elif kind == "formula":
            # Try “name: expression” or just expression
            m = _TERM_DEF_SPLIT.match(blob)
            if m:
                nm, expr = m.group(1).strip(), m.group(2).strip()
            else:
                nm, expr = "Formula", blob
            items.append({
                "type":"formula", "name": nm, "expression": expr,
                "source_excerpt": blob[:180], "page": page, "ordinal": ordinal
            })
        else:
            # Bullet or plain text → attempt term:def first, else concept
            m = _TERM_DEF_SPLIT.match(blob)
            if m:
                term, definition = m.group(1).strip(), m.group(2).strip()
                items.append({
                    "type":"definition", "term": term, "definition": definition,
                    "source_excerpt": blob[:180], "page": page, "ordinal": ordinal
                })
            else:
                items.append({
                    "type":"concept", "term": "", "definition": blob,
                    "source_excerpt": blob[:180], "page": page, "ordinal": ordinal
                })
        ordinal += 1
        buffer.clear()

    for ln in _lines(section_text):
        if _BULLET.match(ln) or _EXAMPLE_HINT.search(ln) or _TERM_DEF_SPLIT.match(ln) or _FORMULA_HINT.search(ln):
            if buffer: flush_buffer()
            # strip bullet marker
            ln = re.sub(_BULLET, "", ln)
            buffer.append(ln)
            flush_buffer()
        else:
            # heading echoes or plain text lines—skip or attach to previous if present
            # If you want to absorb freeform paragraphs, uncomment:
            # buffer.append(ln)
            # flush_buffer()
            continue
    flush_buffer()
    return items

def build_template_from_chunks(chunks: List[Tuple[str,int]], *, title: str = "Untitled") -> Dict[str,Any]:
    doc_text, spans = _concat(chunks)
    # Discover headings from the document itself (works for your calculus guide)
    sections = _split_by_headings(doc_text, spans, titles=None)

    ordinal = 1
    out_sections = []
    for sec in sections:
        items = _build_items_for_section(sec["text"], sec["page_start"], ordinal)
        ordinal += len(items)
        out_sections.append({
            "title": sec["title"],
            "page_start": sec["page_start"],
            "page_end": sec["page_end"],
            "items": items
        })

    toc = []
    for s in out_sections:
        first_ord = s["items"][0]["ordinal"] if s["items"] else ordinal
        toc.append({
            "title": s["title"],
            "page_start": s["page_start"],
            "page_end": s["page_end"],
            "ordinal_first": first_ord
        })

    return {
        "version": "study-template/v1",
        "title": title,
        "pages": spans[-1][2] if spans else 1,
        "sections": out_sections,
        "toc": toc
    }
