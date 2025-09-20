# flashcards/ai/driver.py
from __future__ import annotations
from pathlib import Path
import fitz  # PyMuPDF
from .analysis import analyze_document

def _trim_by_chars(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 1)]

def run_extraction(path: Path, max_tokens: int = 500):
    """
    Return a list of (text, page_start, section_title|None).

    Prefer section-based chunks using the PDF TOC. If no TOC is present,
    fall back to per-page chunks.
    """
    # crude but safe: ~6 chars per token budget for each chunk
    max_chars = max(2000, int(max_tokens * 6))

    stats = analyze_document(path)
    toc_sections = stats.get("toc_sections") or []

    doc = fitz.open(path.as_posix())
    chunks: list[tuple[str, int, str | None]] = []

    if toc_sections:
        # One chunk per TOC section (title + its page range)
        for s in toc_sections:
            title = s["title"]
            start = int(s["page_start"])
            end   = int(s["page_end"])
            parts: list[str] = []
            for p in range(start - 1, end):
                try:
                    txt = doc.load_page(p).get_text("text") or ""
                except Exception:
                    txt = ""
                if txt:
                    parts.append(txt)
            content = "\n".join(parts).strip()
            if content:
                chunks.append((_trim_by_chars(content, max_chars), start, title))
    else:
        # Fallback: chunk per page
        for i in range(doc.page_count):
            try:
                txt = doc.load_page(i).get_text("text") or ""
            except Exception:
                txt = ""
            txt = txt.strip()
            if txt:
                chunks.append((_trim_by_chars(txt, max_chars), i + 1, None))

    doc.close()
    return chunks
