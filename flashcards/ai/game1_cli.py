# game1_cli.py — “Curate & Improve”
import json, random, pathlib, argparse, pickle, os
from typing import List, Tuple

HAND_MIN, HAND_MAX = 3, 12
PROFILE_PATH = pathlib.Path("user_profile.json")


# ── Helpers ───────────────────────────────────────────────────────────────
def load_cards(json_file: str) -> List[dict]:
    data = json.loads(pathlib.Path(json_file).read_text(encoding="utf-8"))
    for i, c in enumerate(data):
        c.setdefault("id", f"c{i}")
    return data


def load_profile() -> dict:
    if PROFILE_PATH.exists():
        return json.loads(PROFILE_PATH.read_text())
    # simple starter profile
    return {"kept": 0, "tossed": 0, "by_card": {}}


def save_profile(profile: dict) -> None:
    PROFILE_PATH.write_text(json.dumps(profile, indent=2))


def gpt_refurbish(kept: List[dict], tossed: List[dict]) -> List[dict]:
    """
    Stub replacement for the actual GPT call.
    For now, we just paraphrase tossed card fronts with '(improved)' suffix.
    """
    new_cards = []
    for c in tossed:
        new_cards.append(
            {
                "id": c["id"] + "_v2",
                "front": c["front"] + " (improved)",
                "back": c["back"],
            }
        )
    return new_cards


# ── Main game loop ────────────────────────────────────────────────────────
def play_curate(cards: List[dict], source_file: str, *, endless: bool = False):
    pool = cards[:]               # mutable copy
    kept_deck = []
    graveyard = []
    profile = load_profile()

    round_no = 1
    while True:
        print(f"\n=== Round {round_no} ===")
        hand_size = random.randint(HAND_MIN, HAND_MAX)
        hand = random.sample(pool, min(hand_size, len(pool)))

        kept, tossed = [], []
        for idx, card in enumerate(hand, 1):
            print(f"\n── {idx}/{len(hand)} ──")
            print("Q:", card["front"])
            print("A:", card["back"])
            choice = input("(k)eep / (t)hrow :").lower().strip()
            if choice.startswith("k"):
                kept.append(card)
            else:
                tossed.append(card)
                reason = input("Reason (optional): ")
            
        # update piles and profile
        kept_deck.extend(kept)
        graveyard.extend(tossed)
        profile["kept"] += len(kept)
        profile["tossed"] += len(tossed)
        for card in kept + tossed:
            stat = profile["by_card"].setdefault(card["id"], {"k": 0, "t": 0})
            if card in kept:
                stat["k"] += 1
            else:
                stat["t"] += 1
        save_profile(profile)

        if not tossed or not pool:
            break  # player happy or pool exhausted

        # refurbish tossed cards with GPT logic
        new_cards = gpt_refurbish(kept, tossed)
        pool = [c for c in pool if c not in hand] + new_cards
        round_no += 1

    # ── Final summary ─────────────────────────────────────────────────
    print("\n🎯  Session finished.")
    print(f"Total kept: {len(kept_deck)}   |   Total tossed: {len(graveyard)}")
    print("\nPer‑card stats this session:")
    for card in kept_deck + graveyard:
        s = profile["by_card"][card["id"]]
        print(f"- {card['front'][:60]}…   ✔ {s['k']}   ✘ {s['t']}")
    
    # --- Write session TXT -----------------------------------------------
    
        # --- Write session TXT -------------------------------------------
    base = pathlib.Path(source_file).stem.rsplit(".", 1)[0]
    sess_path = pathlib.Path(base + "_session.txt")

    report = []
    for c in kept_deck:
        report.append("[KEPT]   " + c["front"] + "  ->  " + c["back"])
    for c in graveyard:
        report.append("[THROWN] " + c["front"] + "  ->  " + c["back"])

    sess_path.write_text("\n\n".join(report), encoding="utf-8")
    print(f"\n📝  Session log written → {sess_path.resolve()}")


    print("\nThank you — Game 1 over!\n")

