# flashcards/ai/flashcard_gen.py
from __future__ import annotations
import json, logging
from typing import List
from openai import OpenAI

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """
You are an expert flash-card author for general study materials.

Create high-quality, *atomic* cards (one fact/idea each) that help a learner recall and apply core concepts from the given text chunk. Avoid vague wording, pronouns without clear referents, trivial copies of headings, and True/False.

Card types to mix: definition/term, concept→example, example→concept, steps of a process, cause→effect, compare/contrast, cloze (fill-in-the-blank), light recall of dates/formulas where central. Prefer Understand/Apply levels, with a few Remember/Analyze.

Rules:
• The *front* must be a clear, self-contained question (≤ 20 words).
• The *back* is the exact answer (≤ 25 words), normalized (units/terms) and unambiguous.
• Add 2 plausible *distractors* (same type/units/scale; no “all/none of the above”).
• Include a short *excerpt* (≤ 80 words) copied or tightly paraphrased from the chunk that supports the answer. If you must paraphrase, keep it faithful to the source.
• Use a general *context* tag from this set:
  "definition" | "concept" | "process" | "example" | "comparison" | "timeline" | "formula" | "other"
• If a page/section indicator is provided separately, set an integer *page* accordingly; otherwise omit it.
• Deduplicate: do not emit near-identical fronts; skip low-value cards.

Return **only** a JSON object exactly in this schema and nothing else:

{
  "cards": [
    {
      "front": "string",
      "back": "string",
      "excerpt": "string",
      "distractors": ["str","str"],
      "context": "definition | concept | process | example | comparison | timeline | formula | other",
      "page": 12
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
