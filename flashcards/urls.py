# flashcards/urls.py
from django.urls import path
from . import views

urlpatterns = [
    path("generate/", views.generate_deck, name="generate_deck"),
    path("hand/",      views.hand,         name="hand"),
    path("feedback/",  views.feedback,     name="feedback"),
]
