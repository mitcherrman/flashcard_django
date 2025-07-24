"""
cli.py – terminal entry point
Usage:
$ python -m flashcards.pipeline.cli my.pdf --sample 2
"""
from __future__ import annotations
import argparse, sys, pathlib, logging
from .core import build_deck_files, run_pdf_to_cards
from game1_cli import play_curate
from game2_cli import play_basic, play_mc, load_cards

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

def main() -> None:
    ap = argparse.ArgumentParser("flash‑card pipeline + games")
    ap.add_argument("file", type=pathlib.Path,
                    help="PDF / DOCX / TXT OR an existing .cards.json")
    ap.add_argument("--tokens", type=int, default=900)
    ap.add_argument("--cards", type=int, default=3)
    ap.add_argument("--sample", type=int, help="Sample N random chunks")
    ap.add_argument("--endless", action="store_true")
    args = ap.parse_args()

    # ---- if user passes .json, skip build phase -------------------------
    if args.file.suffix == ".json":
        json_path = args.file
        print(f"Using existing cards JSON: {json_path.name}")
    else:
        deck_path, json_path = build_deck_files(
            args.file,
            max_tokens=args.tokens,
            max_cards_per_chunk=args.cards,
            sample_chunks=args.sample,
        )
        print(f"Deck:  {deck_path.name}")
        print(f"JSON:  {json_path.name}")

    # ---- choose game ----------------------------------------------------
    game = input("Game 1 (curate) or 2 (drill)? [1/2] ").strip()
    if game == "1":
        play_curate(load_cards(json_path), str(json_path))
    elif game == "2":
        mode = input("Mode: basic (1) or MC (2)? ").strip()
        fn = play_mc if mode == "2" else play_basic
        fn(load_cards(json_path), endless=args.endless)
    else:
        print("Cancelled.")

if __name__ == "__main__":
    main()
