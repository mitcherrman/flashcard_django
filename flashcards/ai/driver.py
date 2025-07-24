# driver.py ---------------------------------------------------------------
import os
from pathlib import Path
from ingest import extract_text
from chunker import make_chunks

print("in driver")

def run_extraction(path: Path, max_tokens: int = 900):
    """Return a list[str] of GPT-sized chunks from a document."""
    raw = extract_text(path)
    chunks = make_chunks(raw, max_tokens=max_tokens)
    print(f"[driver] {len(chunks)} chunks (≈ {sum(len(c) for c in chunks)//1000}k chars)")
    return chunks