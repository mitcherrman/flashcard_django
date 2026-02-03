# flashcards/tasks.py
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from celery import shared_task, states
from celery.exceptions import Ignore
from django.core.cache import cache
from django.db import transaction
from django.contrib.auth import get_user_model
from django.conf import settings

from .models import Deck, Card

# Your pipeline utilities (adjust imports if your paths differ)
from .ai.analysis import analyze_document
from .ai.pipeline.core import cards_from_document
from .ai.flashcard_gen import build_card_key

log = logging.getLogger(__name__)
User = get_user_model()

# ──────────────────────────────────────────────────────────────────────────────
# Progress helpers (cache + Celery meta)
# ──────────────────────────────────────────────────────────────────────────────

def _progress_key(task_id: str) -> str:
    return f"task_progress:{task_id}"

def _set_progress(task_id: str, msg: str) -> None:
    cache.set(_progress_key(task_id), msg, timeout=60 * 60)  # 1 hour

def _get_progress(task_id: str) -> str:
    return cache.get(_progress_key(task_id), "")

def _update_progress(self, msg: str) -> None:
    # Update both Celery state meta and our cache (status endpoint may use either)
    try:
        self.update_state(state="STARTED", meta={"progress": msg})
    except Exception:
        pass
    _set_progress(self.request.id, msg)


# ──────────────────────────────────────────────────────────────────────────────
# Template build (sections) → /template/build/ + /template/status/
# ──────────────────────────────────────────────────────────────────────────────
@shared_task(bind=True)
def build_template_task(self, file_path: str, filename: str) -> Dict[str, Any]:
    """
    Analyze PDF and return:
      {
        pages, words, recommended_cards, suggested_range: {lo,hi},
        per_section_allocation: [{title,page_start,page_end,share,cards}, ...]
      }
    """
    task_id = self.request.id
    try:
        self.update_state(state="STARTED", meta={"progress": "parsing…"})

        result = analyze_document(Path(file_path))

        self.update_state(state="PROGRESS", meta={"progress": "finalizing…"})

        return result
    except Exception as e:
        import logging
        logging.getLogger(__name__).exception("Template build failed")
        raise

    except Exception as e:
        log.exception("Template build failed")
        self.update_state(state=states.FAILURE, meta={"progress": f"Error: {e}"})
        _set_progress(task_id, f"Error: {e}")
        raise Ignore()


# ──────────────────────────────────────────────────────────────────────────────
# Deck build (cards) → /build/deck/ + /build/deck/status/<job_id>/
# ──────────────────────────────────────────────────────────────────────────────

def _fake_build_cards(pdf_path: str, allocations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Simple fallback so you can test the async flow without running the full NLP.
    Produces n dummy Q/A per section based on the requested allocation.
    """
    cards: List[Dict[str, Any]] = []
    ordinal = 1
    for sec in allocations:
        make_n = int(sec.get("cards") or 0)
        title  = sec.get("title") or "Section"
        p0 = sec.get("page_start") or 1
        for i in range(make_n):
            cards.append({
                "front": f"{title}: Q{i+1}",
                "back":  f"Answer for {title} #{i+1}",
                "excerpt": "",
                "context": "",
                "page": p0,
                "section": title,
                "distractors": [],
            })
            ordinal += 1
    return cards


@shared_task(bind=True)
def build_deck_task(
    self,
    pdf_path: str,
    deck_name: str,
    allocations: List[Dict[str, Any]],
    coverage: str = "even",
    cards_wanted: Optional[int] = None,
    user_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Generate cards from `pdf_path` using the selected `allocations`, create a Deck,
    and insert Card rows. Returns: { deck_id, cards_count, created: true }
    """
    task_id = self.request.id
    try:
        if not allocations:
            raise ValueError("No section allocations were provided")

        _update_progress(self, "Planning card generation…")

        # Try real pipeline first; if it fails, fall back to a fake builder in DEBUG
        generated: List[Dict[str, Any]]
        try:
            _update_progress(self, "Extracting content & generating Q/A…")
            generated = cards_from_document(
                Path(pdf_path),
                total_cards=cards_wanted,      # use what the client asked for
                sections_plan=allocations,     # your per-section plan
                concurrency=5,                 # keep 1 on Windows for stability
                max_cards_per_section=3,       # reasonable cap per section
                max_tokens=750,                # give the model more room than fast mode
                cache_chunks=True,             # reuse extracted chunks when possible
            )
            print(f"cards_from_document returned {len(generated)} items")
            if not isinstance(generated, list) or not all(isinstance(c, dict) for c in generated):
                raise ValueError("cards_from_document must return a list[dict]")
            if not all(isinstance(c, dict) for c in generated):
                raise ValueError("cards_from_document items must be dicts")
        
        except Exception as e:
            log.exception("cards_from_document failed")
            raise

        _update_progress(self, f"Saving deck ({len(generated)} cards)…")

        user = None
        if user_id:
            try:
                user = User.objects.get(id=user_id)
            except User.DoesNotExist:
                user = None

        with transaction.atomic():
            deck = Deck.objects.create(user=user, name=deck_name or (Path(pdf_path).stem[:190] or "My Deck"))

            ordinal = 0
            card_objs: List[Card] = []
            for c in generated:
                front = (c.get("front") or "").strip()
                back  = (c.get("back") or "").strip()
                if not front or not back:
                    continue
                ordinal += 1
                card_key = build_card_key(front, back)
                card_objs.append(Card(
                    deck=deck,
                    front=front,
                    back=back,
                    excerpt=(c.get("excerpt") or "").strip(),
                    context=(c.get("context") or "").strip(),
                    page=c.get("page"),
                    section=(c.get("section") or "").strip(),
                    distractors=c.get("distractors") or [],
                    card_key=card_key,
                    ordinal=ordinal,
                ))

            if card_objs:
                Card.objects.bulk_create(card_objs, batch_size=500)

        _update_progress(self, f"Completed: {ordinal} cards.")
        return {"deck_id": deck.id, "cards_count": ordinal, "created": True}

    except Exception as e:
        log.exception("Deck build failed")
        self.update_state(state=states.FAILURE, meta={"progress": f"Error: {e}"})
        _set_progress(task_id, f"Error: {e}")
        raise Ignore()
