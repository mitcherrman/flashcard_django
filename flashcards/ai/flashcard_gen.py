# flashcards/ai/flashcard_gen.py
from __future__ import annotations
import json, logging
from typing import List
from openai import OpenAI

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """
You are an expert flash‑card author.

Return **only** a JSON object matching *exactly* this schema
and nothing else:

{
  "cards": [
    {
      "front": "string",
      "back":  "string",
      "excerpt": "string",          // ≤ 100 words copied from the chunk
      "distractors": ["str","str"], // two plausible wrong answers
      "context": "event | equipment | party-fact | timeline | admission | other",
      "page": 12                    // integer page number (supplied below)
    }
  ]
}

Max items in "cards": MAX_CARDS
""".strip()


def _cards_from_chunk(
    chunk_text: str,
    page_no: int,
    max_cards: int = 3,
) -> List[dict]:
    """
    • Adds PAGE N prefix so GPT knows the origin.
    • Guarantees each card has "page".
    • Returns [] on any failure.
    """
    prompt = SYSTEM_PROMPT.replace("MAX_CARDS", str(max_cards))
    client = OpenAI()                      # needs OPENAI_API_KEY in env

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": f"PAGE {page_no}\n\n{chunk_text}",
            },
        ],
        response_format={"type": "json_object"},
        max_tokens=900,
    )

    raw = resp.choices[0].message.content
    log.debug("OpenAI raw content (page %s)… %s", page_no, raw[:400])

    try:
        cards = json.loads(raw)["cards"]
    except Exception as exc:
        log.warning("GPT JSON parse failed on page %s: %s", page_no, exc)
        return []

    #  guarantee page number
    for c in cards:
        c.setdefault("page", page_no)
    return cards[:max_cards] if isinstance(cards, list) else []
