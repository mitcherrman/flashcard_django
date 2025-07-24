"""
chunker.py
----------
Split long documents into GPT-friendly chunks, and (optionally) turn those
chunks into embedding vectors.  *No OpenAI client is created at import time*,
so importing this module never fails even if the API key is not yet in the
environment.

Typical usage
-------------
>>> from ingest import extract_text
>>> from chunker import make_chunks, embed_chunks
>>> txt = extract_text("my_doc.pdf")
>>> pieces = make_chunks(txt, max_tokens=700)
>>> vectors = embed_chunks(pieces)   # only if you actually need them
"""

print("in chunker")

# ── 1. Imports ──────────────────────────────────────────────────────────────
from typing import List
import tiktoken  # Official OpenAI tokenizer

# Choose a tokenizer that matches your target model family
enc = tiktoken.encoding_for_model("gpt-4o-mini")  # falls back to cl100k_base

# ── 2. Chunker ─────────────────────────────────────────────────────────────

def make_chunks(text: str, max_tokens: int = 900) -> List[str]:
    """Greedy, paragraph‑aware splitter.

    1. Keeps whole paragraphs together (splits on blank lines).
    2. Ensures no chunk exceeds *approximately* ``max_tokens``.
    3. Returns a list of clean text strings ready for an LLM prompt or
       embedding call.
    """
    paragraphs = text.split("\n\n")  # 2‑A  Heuristic paragraph split

    chunks, buffer, token_tally = [], [], 0
    for para in paragraphs:
        ptokens = len(enc.encode(para))

        # Would this paragraph overflow the current buffer?
        if token_tally + ptokens > max_tokens and buffer:
            chunks.append("\n\n".join(buffer))
            buffer, token_tally = [], 0

        buffer.append(para)
        token_tally += ptokens

    if buffer:
        chunks.append("\n\n".join(buffer))

    return chunks

# ── 3. Optional: compute embeddings for semantic search ────────────────────

def embed_chunks(chunks: List[str], model: str = "text-embedding-3-large") -> List[List[float]]:
    """Convert each chunk to an embedding vector using OpenAI.

    A *lazy* OpenAI client is created **inside** this function, so importing
    ``chunker`` never triggers a network call or requires the API key.
    """
    from openai import OpenAI  # Local import → no API call at import time

    client = OpenAI()  # Reads ``OPENAI_API_KEY`` from the environment

    vectors: List[List[float]] = []
    for text in chunks:
        res = client.embeddings.create(model=model, input=text)
        vectors.append(res.data[0].embedding)

    return vectors
