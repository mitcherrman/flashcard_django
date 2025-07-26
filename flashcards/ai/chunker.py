"""
chunker.py – split long text into ≈max_tokens chunks.
Pure library: no IO, no prints.
"""
from typing import List
import tiktoken
import logging

log = logging.getLogger(__name__)
enc = tiktoken.encoding_for_model("gpt-4o-mini")   # falls back if unknown


def make_chunks(text: str, max_tokens: int = 500) -> List[str]:
    paragraphs = text.split("\n\n")
    chunks, buf, tally = [], [], 0

    for para in paragraphs:
        tok = len(enc.encode(para))
        if tally + tok > max_tokens and buf:
            chunks.append("\n\n".join(buf))
            buf, tally = [], 0
        buf.append(para)
        tally += tok

    if buf:
        chunks.append("\n\n".join(buf))

    log.debug("chunker → %s chunks (≈%sk chars)",
              len(chunks), sum(len(c) for c in chunks) // 1000)
    return chunks
