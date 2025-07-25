# flashcards/ai/pipeline/__init__.py
"""
Re‑export helpers so other code can keep the old names.
"""

from .core import (
    cards_from_document as run_pdf_to_cards,
    write_json_for_document as build_deck_files,
)

__all__ = [
    "run_pdf_to_cards",
    "build_deck_files",
]
