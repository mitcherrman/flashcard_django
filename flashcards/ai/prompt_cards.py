import json, pathlib, genanki, random
from openai import OpenAI
from fastapi.responses import FileResponse

def generate_from_prompt(topic: str, num_cards: int) -> FileResponse:
    
    client = OpenAI()
    
    # Prompt to instruct the AI to generate flashcards in JSON format
    prompt = f"""
You are an expert flash-card author for general study.

Create high-quality, *atomic* cards that cover the core ideas of the given topic. Use an Understand/Apply focus with a few Remember/Analyze items. Avoid trivia, vague wording, and True/False.

Card styles to include across the set:
• Definition/term
• Concept→example and example→concept
• Steps of a process (short sequences only)
• Cause→effect or “what happens if…”
• Compare/contrast between similar terms
• Cloze deletions for key facts/formulas

Quality rules:
• Front: clear, self-contained question (≤ 20 words), no pronouns without nouns.
• Back: exact, concise answer (≤ 25 words), normalized terms/units.
• Distractors: 2 plausible, type-matched, common misconceptions; no “all/none of the above”.
• Deduplicate similar fronts; skip low-value items.

Return **only** JSON shaped like:

{
  "cards": [
    {
      "front": "string",
      "back": "string",
      "distractors": ["str","str"],
      "context": "definition | concept | process | example | comparison | timeline | formula | other"
    }
  ]
}

Limit to **MAX_CARDS** cards.

        """

    # Call OpenAI chat completion, replace values within prompt with user inputted arguments
    response = client.chat.completions.create(
        model = "gpt-4o-mini",
        messages = [
            {"role": "system", "content": prompt.replace("{{num_cards}}", str(num_cards)).replace("{{topic}}", topic)},
            {"role": "user", "content": f"The topic is {topic} and I only want to make {num_cards} cards"}
        ],
        response_format = {"type": "json_object"} # Expect only JSON objects
        
    )
    
    cards = json.loads(response.choices[0].message.content)["cards"] # Parse "cards"
    output_path = create_anki_deck(cards, topic)
    return cards # For testing
    # return FileResponse(output_path, filename="flashcards.apkg", media_type="application/octet-stream") # Returns anki deck downloadable

# Create an Anki deck file from "cards" and return the file path
def create_anki_deck(response: list[dict], topic: str) -> str:
    
    # Model format for each note (flashcard)
    my_model = genanki.Model(
        1537156451, # Model ID
        'Flashcard Model',
        fields=[
            {'name': 'Question'},
            {'name': 'Answer'},
        ],
        templates=[ # CSS format for UI implementation
            {
            'name': 'Card 1',
            'qfmt': '{{Question}}',
            'afmt': '{{FrontSide}}<hr id="answer">{{Answer}}',
            },
        ])
    
    # Decks to store flashcards
    my_deck = genanki.Deck(
        random.randrange(1 << 30, 1 << 31), # Randomized deck ID (avoid overwrite when downloading decks)
        topic)
    
    # Transfer API "cards" to flashcards 
    for i in range(len(response)):
        my_note = genanki.Note(
            model = my_model,
            fields = [response[i]["front"], response[i]["back"]]
        )
        my_deck.add_note(my_note) # Adds each note as a flashcard to the deck
        
    output_path = pathlib.Path(f"flashcards_{random.randint(100000,999999)}.apkg") # Avoids Overwrite during simultanious downloads
    genanki.Package(my_deck).write_to_file(output_path) # Generates .apkg file
    return output_path