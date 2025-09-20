# flashcards/ai/analysis.py
from __future__ import annotations
import logging, re
from pathlib import Path
import fitz  # PyMuPDF

log = logging.getLogger(__name__)

WORD_RE = re.compile(r"\b[\w\-’']+\b", re.UNICODE)

def _count_words(text: str) -> int:
    return len(WORD_RE.findall(text))

def analyze_document(path: Path) -> dict:
    """
    Fast, no-LLM inspection for the UI and backend:
      • pages, words, words/page
      • toc_sections: [{title, page_start, page_end, words}]
      • recommended_cards and suggested_range (primary driver = #sections)
      • per_section_allocation at the recommended count
    """
    doc = fitz.open(path)
    pages = doc.page_count

    words_per_page: list[int] = []
    for i in range(pages):
        txt = doc.load_page(i).get_text("text") or ""
        words_per_page.append(_count_words(txt))

    total_words = sum(words_per_page)

    # ---- TOC → sections with contiguous page spans ----
    try:
        toc = doc.get_toc() or []   # [[level, title, page], ...], page is 1-based
    except Exception:
        toc = []
    doc.close()

    flat = [
        {"title": t, "page_start": p, "level": lvl}
        for (lvl, t, p) in toc
        if p >= 1 and p <= pages
    ]
    flat.sort(key=lambda x: x["page_start"])

    sections: list[dict] = []
    for i, s in enumerate(flat):
        start = s["page_start"]
        end   = (flat[i + 1]["page_start"] - 1) if i + 1 < len(flat) else pages
        end   = max(start, end)
        w = sum(words_per_page[start - 1 : end]) if pages else 0
        sections.append({
            "title": s["title"],
            "page_start": start,
            "page_end": end,
            "words": w,
        })

    sections_count = len(sections) if sections else 0

    # ---- Recommendation (primarily sections-driven) ----
    def clamp(v, lo, hi): return max(lo, min(hi, v))

    if sections_count > 0:
        # One card per section as the baseline, bounded to [3, 30].
        base = sections_count
    else:
        # No TOC? Fall back to length (coarse).
        base = round(total_words / 200)  # ~1/200 words

    recommended = int(clamp(base, 3, 30))
    lo = int(clamp(round(recommended * 0.75), 3, 30))
    hi = int(clamp(round(recommended * 1.25), 3, 30))

    # ---- Per-section allocation at the recommended count ----
    per_section = []
    if sections_count and recommended:
        # Give everyone 1 first (breadth), then distribute leftovers by word share
        remaining = max(0, recommended - sections_count)
        total_sec_words = sum(s["words"] for s in sections) or sections_count

        prelim = []
        for s in sections:
            share = (s["words"] or 1) / total_sec_words
            prelim.append(1 + round(remaining * share))

        # Normalize to exactly 'recommended'
        delta = recommended - sum(prelim)
        i = 0
        while delta != 0 and prelim:
            j = i % len(prelim)
            if delta > 0:
                prelim[j] += 1; delta -= 1
            else:
                if prelim[j] > 0:
                    prelim[j] -= 1; delta += 1
            i += 1

        for s, k in zip(sections, prelim):
            per_section.append({
                "title": s["title"],
                "page_start": s["page_start"],
                "page_end": s["page_end"],
                "words": s["words"],
                "cards": int(k),
            })

    return {
        "pages": pages,
        "words": total_words,
        "words_per_page": words_per_page,
        "sections_count": sections_count,
        "toc_sections": sections,                 # may be []
        "recommended_cards": recommended,
        "suggested_range": {"lo": lo, "hi": hi},
        "per_section_allocation": per_section,    # may be []
    }
