"""
Generate **JSON cards only** – no Anki deck anymore
"""
from __future__ import annotations
import json, logging, os, pathlib
from typing import List
from openai import OpenAI

log = logging.getLogger(__name__)


def _get_client() -> OpenAI:
    return OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def _cards_from_chunk(chunk: str, max_cards: int = 3) -> List[dict]:
    SYSTEM_PROMPT = """(same long prompt, keep unchanged)"""
    client = _get_client()
    resp   = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT.format(max_cards=max_cards)},
            {"role": "user",   "content": chunk},
        ],
        response_format={"type": "json_object"},
        max_tokens=900,
    )
    try:
        return json.loads(resp.choices[0].message.content)["cards"][:max_cards]
    except Exception as exc:
        log.warning("GPT JSON parse error: %s", exc)
        return []


def build_json(chunks: List[str], deck_name: str,
               max_cards_per_chunk: int = 3) -> pathlib.Path:
    all_cards: list[dict] = []
    for i, ch in enumerate(chunks, 1):
        new = _cards_from_chunk(ch, max_cards_per_chunk)
        log.info("Chunk %s/%s → %s card(s)", i, len(chunks), len(new))
        all_cards.extend(new)

    path = pathlib.Path(deck_name.replace(" ", "_") + ".cards.json")
    path.write_text(json.dumps(all_cards, ensure_ascii=False, indent=2), "utf‑8")
    log.info("Card JSON written → %s (%s cards)", path, len(all_cards))
    return path
