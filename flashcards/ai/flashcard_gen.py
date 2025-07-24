# flashcard_gen.py --------------------------------------------------------
import os, json, random, pathlib, sys
from typing import List
from openai import OpenAI
import genanki

print("in flashcard_gen")

def get_client():
    from openai import OpenAI
    import os
    return OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def _cards_from_chunk(chunk: str, max_cards: int = 3):
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
  "excerpt"   : "<verbatim or lightly‑cleaned quote (≤ 100 words)>",
  "front"     : "<Question answerable from excerpt>",
  "back"      : "<Correct answer>",
  "distractors": ["Wrong A", "Wrong B"],
  "context"   : "event" | "equipment" | "party-fact" | "timeline" | "admission" | "other"
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

Limit to {max_cards} cards.
"""
    # --- build the system message ---
    system_msg = SYSTEM_PROMPT.format(max_cards=max_cards)

    client = get_client()
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user",   "content": chunk},
        ],
        response_format={"type": "json_object"},
        max_tokens=900,
    )

    # ---------- safe JSON parse ----------
    try:
        cards = json.loads(resp.choices[0].message.content)["cards"]
    except Exception as e:
        print("[flashcard_gen] ⚠️  GPT returned malformed JSON:", e)
        cards = []

    # guarantee it is a list of dicts
    cards = cards[:max_cards] if isinstance(cards, list) else []
    return cards

# Call in _cards_from_chunk if using anki
def build_deck(chunks: List[str], deck_name: str,
               max_cards_per_chunk: int = 3) -> pathlib.Path:
    """Return Path to the generated .apkg file and also write a .cards.json file."""
    all_cards: list[dict] = []
    for i, ch in enumerate(chunks, 1):
        new_cards = _cards_from_chunk(ch, max_cards_per_chunk)
        print(f"[flashcard_gen] Chunk {i}/{len(chunks)} → {len(new_cards)} card(s)")
        all_cards.extend(new_cards)
    total_cards = len(all_cards)
    print(f"[flashcard_gen] Total cards generated: {total_cards}")

    # --- JSON dump for Game 2 ---
    json_path = pathlib.Path(deck_name.replace(" ", "_") + ".cards.json")
    json_path.write_text(json.dumps(all_cards, ensure_ascii=False, indent=2), encoding="utf-8")
    print("[flashcard_gen] Card JSON written →", json_path)

    # --- Human‑readable TXT dump ------------------------------------------
    txt_lines = []
    for c in all_cards:
        txt_lines.append("Q: " + c["front"])
        txt_lines.append("A: " + c["back"])
        txt_lines.append("CTX: " + c.get("context", ""))
        txt_lines.append("EXCERPT: " + c.get("excerpt", "")[:200])
        txt_lines.append("-" * 40)
    txt_path = pathlib.Path(deck_name.replace(" ", "_") + ".cards.txt")
    txt_path.write_text("\n".join(txt_lines), encoding="utf-8")
    print("[flashcard_gen] Card TXT written →", txt_path)

    deck = genanki.Deck(random.randrange(1<<30), deck_name[:90])
    model = genanki.Model(
        1537156452, "Basic",
        fields=[{"name":"Front"},{"name":"Back"}],
        templates=[{"name":"Card",
                    "qfmt":"{{Front}}",
                    "afmt":"{{Back}}<hr id=answer>"}],
    )
    for c in all_cards:
        # skip malformed card if GPT failed to supply two distractors
        if len(c.get("distractors", [])) < 2:
            continue
        deck.add_note(genanki.Note(model, [c["front"], c["back"]]))


    out = pathlib.Path(deck_name.replace(" ", "_") + ".apkg")
    genanki.Package(deck).write_to_file(out)
    print("[flashcard_gen] Deck written →", out.resolve())
    return out