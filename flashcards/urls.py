# flashcards/urls.py
from django.urls import path
from . import views

urlpatterns = [
    # New quick analysis endpoint
    path("analyze/",  views.analyze,       name="analyze"),

    # Back-compat alias (optional): keep old /inspect/ working
    path("inspect/",  views.analyze,       name="inspect"),

    # Deck building + gameplay
    path("generate/", views.generate_deck, name="generate"),
    path("hand/",     views.hand,          name="hand"),
    path("feedback/", views.feedback,      name="feedback"),
    path("health/",   views.health,        name="health"),
]
