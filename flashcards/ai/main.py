import os
from decouple import config
os.environ.setdefault("OPENAI_API_KEY", config("OPENAI_API_KEY"))

import json
# from prompt_cards import generate_from_prompt
# from ingest import extract_text
from flashcards.ai import game
import subprocess



'''
def main(option: str):    
    if option == "from_pdf":
        path = input("File path")
        extract_text(path)
    elif option == "from_prompt":
        topic = input("topic")
        num_cards = int(input("num_cards_to_generate"))
        generate_from_prompt(topic, num_cards)


if __name__ == "__main__":
    os.environ.setdefault("OPENAI_API_KEY", config("OPENAI_API_KEY"))
    main("user option placeholder")
'''


# testing each option seperately
if __name__ == "__main__":
    print("running pipeline")
    subprocess.run(["python", "C:/Users/ethan/flashcard-maker-3/pipeline.py"]) # Change file path to file to be tested
    game()