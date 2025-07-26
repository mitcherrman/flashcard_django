# flashcards/models.py
from django.db import models
from django.contrib.auth import get_user_model
User = get_user_model()

class Upload(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    uploaded_file = models.FileField(upload_to = "pdfs/", blank = True, null = True)
    uploaded_at = models.DateTimeField(auto_now_add = True)
    source_type = models.CharField(max_length=200)
    prompt_text = models.TextField(blank = True, null = True)
    

class Deck(models.Model):
    user = models.ForeignKey(User, related_name = "cards", on_delete=models.CASCADE)
    name = models.CharField(max_length=200)
    created = models.DateTimeField(auto_now_add=True)
    source = models.ForeignKey(Upload, on_delete = models.SET_NULL, null = True, blank = True)

class Card(models.Model):
    deck  = models.ForeignKey(Deck, on_delete=models.CASCADE)
    front = models.TextField()
    back  = models.TextField()
    right = models.IntegerField(default=0)
    wrong = models.IntegerField(default=0)
