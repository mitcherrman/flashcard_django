"""
chunker.py – split (text,page_no) tuples into ≈ max_tokens chunks.
Returns List[tuple[str,int]]   →  (chunk_text, starting_page)
"""
from __future__ import annotations
from typing import List, Tuple
import tiktoken, logging

log = logging.getLogger(__name__)
enc = tiktoken.encoding_for_model("gpt-4o-mini")   # falls back gracefully


def make_chunks(
    pages: List[Tuple[str, int]],       # [(page_text, page_no), …]
    max_tokens: int = 500,
) -> List[Tuple[str, int]]:
    chunks: list[tuple[str, int]] = []

    buf: list[str] = []
    tally = 0
    chunk_start_page = None

    for page_text, page_no in pages:
        for para in page_text.split("\n\n"):
            tok = len(enc.encode(para))

            if tally + tok > max_tokens and buf:
                chunks.append(("\n\n".join(buf), chunk_start_page))
                buf, tally, chunk_start_page = [], 0, None

            if chunk_start_page is None:
                chunk_start_page = page_no

            buf.append(para)
            tally += tok

    if buf:
        chunks.append(("\n\n".join(buf), chunk_start_page))

    log.debug("chunker → %s chunk(s) (≈%sk chars)",
              len(chunks), sum(len(c[0]) for c in chunks) // 1000)
    return chunks
