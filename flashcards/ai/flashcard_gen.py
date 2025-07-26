# flashcards/ai/flashcard_gen.py  – only the top part shown here
# flashcards/ai/flashcard_gen.py
from __future__ import annotations
import json, logging
from typing import List

from openai import OpenAI, OpenAIError

log = logging.getLogger(__name__)


# ------------------------------------------------------------------ #
# 1)  Prompt template (no .format braces except for MAX_CARDS token) #
# ------------------------------------------------------------------ #
SYSTEM_PROMPT = """
You are an expert flash‑card author.

Return **only** a JSON object matching *exactly* this schema
and nothing else:

{
  "cards": [
    {
      "front": "string",
      "back":  "string",
      "excerpt": "string",          // ≤ 100 words from the chunk
      "distractors": ["str","str"], // two plausible wrong answers
      "context": "event | equipment | party-fact | timeline | admission | other"
    }
  ]
}

Max items in "cards": MAX_CARDS
""".strip()


# Choose a default model that is *always* live for every key.
DEFAULT_MODEL = "gpt-4o-mini"

def _cards_from_chunk(chunk: str, max_cards: int = 3) -> List[dict]:
    """
    Return a *list[dict]* for one chunk or [] on any failure.
    Raises RuntimeError with the original OpenAI message so the
    Django view can include it in the HTTP response.
    """
    prompt = SYSTEM_PROMPT.replace("MAX_CARDS", str(max_cards))
    client = OpenAI()                         # requires OPENAI_API_KEY
    resp   = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user",   "content": chunk},
        ],
        response_format={"type": "json_object"},
        max_tokens=900,
    )

    # 🔍 --- NEW: log exactly what we got back -------------------------
    content = resp.choices[0].message.content
    log.debug("OpenAI raw content:\n%s", content[:500])   # first 500 chars

    try:
        cards = json.loads(content)["cards"]
    except Exception as exc:
        log.warning("GPT JSON parse failed: %s", exc)
        cards = []

    return cards[:max_cards] if isinstance(cards, list) else []