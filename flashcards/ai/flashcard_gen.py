from __future__ import annotations
import json, logging, hashlib, re
from typing import List, Optional
from openai import OpenAI

log = logging.getLogger(__name__)

_KEY_RE = re.compile(r"[^a-z0-9]+")
def build_card_key(front: str, back: str) -> str:
    base = f"{front} || {back}".lower()
    base = _KEY_RE.sub(" ", base)
    base = " ".join(base.split())
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:40]

SYSTEM_PROMPT = """
You are an expert flash-card author for general study materials.

Create high-quality, *atomic* cards (one fact/idea each) that help a learner recall and apply core concepts from the given text chunk. Avoid vague wording, pronouns without clear referents, trivial copies of headings, and True/False.

If a SECTION name is provided, ensure each card includes a `"section"` field
with exactly that string.

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
      "page": 12,
      "section": "string"
    }
  ]
}

Max items in "cards": MAX_CARDS
""".strip()

def _ask_openai(chunk_text: str, page_no: int, section: Optional[str], max_cards: int) -> List[dict]:
    """One call to OpenAI that *should* return up to max_cards cards."""
    prompt = SYSTEM_PROMPT.replace("MAX_CARDS", str(max_cards))
    client = OpenAI()

    user_blob = f"PAGE: {page_no}\n"
    if section:
        user_blob += f"SECTION: {section}\n"
    user_blob += "\nTEXT:\n" + chunk_text

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user",   "content": user_blob},
        ],
        response_format={"type": "json_object"},
        max_tokens=1400,  # give breathing room
        temperature=0.2,
    )

    raw = resp.choices[0].message.content or ""
    log.debug("OpenAI raw content (page %s, section=%r)… %s", page_no, section, raw[:400])

    # Try strict parse first
    try:
        obj = json.loads(raw)
        cards = obj.get("cards", [])
        if isinstance(cards, list):
            return cards
        return []
    except Exception as exc:
        log.warning("GPT JSON parse failed on page %s: %s", page_no, exc)

    # Fallback: try to salvage by trimming to last closing bracket
    try:
        start = raw.find("{")
        end   = raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            obj = json.loads(raw[start:end+1])
            cards = obj.get("cards", [])
            if isinstance(cards, list):
                return cards
    except Exception:
        pass

    return []

def _cards_from_chunk(
    chunk_text: str,
    page_no: int,
    section: Optional[str] = None,
    max_cards: int = 3,
) -> List[dict]:
    """
    Robust wrapper:
      - Ask for small batches (<=3) to avoid long/truncated JSON.
      - If a batch still fails, fall back to single-card requests until filled or retries exhausted.
    """
    want = int(max_cards)
    have: List[dict] = []
    # request in batches of 3
    while want > 0:
        batch = min(3, want)
        got = _ask_openai(chunk_text, page_no, section, batch)

        if not isinstance(got, list) or not got:
            # fallback: try single-card retries
            singles_ok = 0
            for _ in range(batch):
                one = _ask_openai(chunk_text, page_no, section, 1)
                if one:
                    have.extend(one[:1])
                    singles_ok += 1
            if singles_ok == 0:
                break  # give up on this chunk
        else:
            have.extend(got[:batch])

        want = max(0, max_cards - len(have))

    # Guarantee metadata
    for c in have:
        c.setdefault("page", page_no)
        if section:
            c.setdefault("section", section)

    return have[:max_cards]
