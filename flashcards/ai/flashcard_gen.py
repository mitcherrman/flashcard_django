# flashcards/ai/flashcard_gen.py  – only the top part shown here
from __future__ import annotations
import json, pathlib, logging
from typing import List
from openai import OpenAI, OpenAIError

log = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
# 1)  Prompt template (no .format braces except for MAX_CARDS token) #
# ------------------------------------------------------------------ #
SYSTEM_PROMPT = """
You are an expert flash‑card author and lawyer preparing study material
for a fellow lawyer who must review the main points of the input document.

## Ignore  
• Court captions, break requests, stenographer chatter,  
  oath pages, counsel appearances, greetings, scheduling logistics.

## Card requirements  
1. Focus on *substantive* facts, breaking down vivid descriptions of events and actions, 
   functionality/malfuction of equipment, description of equipment usage, actions described and points made by the deponent, 
   summary of the main points from the attorneys, admissions, contradictions and other things that matter for pre‑trial review.  
2. For every card return:

{{
  "excerpt"    : "<verbatim or lightly‑cleaned quote (≤ 100 words)>",
  "front"      : "<Question answerable from excerpt>",
  "back"       : "<Correct answer>",
  "distractors": ["Wrong A", "Wrong B"],
  "context"    : "event" | "equipment" | "party-fact" | "timeline" | "admission" | "other"
}}

* “excerpt” must appear in the source chunk.  
* Provide **exactly one** correct answer and **two** plausible but wrong
  answers.  
* Choose a context tag:  
  • **party‑fact**  – statements by a witness/party.  
  • **event**       – description of an incident or action  
  • **equipment**   – functionality, settings, or usage of devices  
  • **admission**   – statements that help or hurt a party’s case  
  • **timeline**    – dates or ordered sequence of events     
  • **other**       – other, not related to previous context tags

Return JSON:  
{{ "cards": [ … ] }}

Limit to **MAX_CARDS** cards.

""".strip()  # ← leave the braces inside the JSON example untouched
# ------------------------------------------------------------------ #
# 2)  Model strategy                                                 #
# ------------------------------------------------------------------ #
MODEL_PRIMARY  = "gpt-4o-mini"          # first attempt (fast / cheap)
MODEL_FALLBACK = "gpt-3.5-turbo-0125"   # always available to every key


def _cards_from_chunk(chunk: str, max_cards: int = 3) -> List[dict]:
    """
    Return up to *max_cards* flash‑card dicts for a single chunk.

    Raises RuntimeError if all model attempts fail.
    """
    prompt = SYSTEM_PROMPT.replace("MAX_CARDS", str(max_cards))
    client = OpenAI()          # relies on OPENAI_API_KEY in env
    last_exc: Exception | None = None

    for model in (MODEL_PRIMARY, MODEL_FALLBACK):
        try:
            res = client.chat.completions.create(
                model=model,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user",   "content": chunk[:8000]},  # stay < 8k
                ],
                timeout=60,
            )
            raw = res.choices[0].message.content
            cards = json.loads(raw)["cards"]
            if isinstance(cards, list) and cards:
                return cards[:max_cards]

            log.warning("%s returned empty card list", model)

        except OpenAIError as exc:
            log.error("OpenAI (%s) error: %s", model, exc)
            last_exc = exc

        except (json.JSONDecodeError, KeyError) as exc:
            log.error("JSON parse error (%s): %s", model, exc)
            last_exc = exc

    # ‑‑ all attempts failed ‑‑
    raise RuntimeError(f"OpenAI failed: {last_exc or 'unknown error'}")