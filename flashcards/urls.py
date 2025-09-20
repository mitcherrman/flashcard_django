# flashcards/urls.py
from django.urls import path
from django.http import JsonResponse
from . import views

urlpatterns = [
    # builder / game API
    path("generate/", views.generate_deck, name="generate_deck"),
    path("hand/",      views.hand,         name="hand"),
    path("feedback/",  views.feedback,     name="feedback"),

    # simple health‑check – reachable at /api/flashcards/health
    path("health/", lambda r: JsonResponse({"ok": True}), name="health"),

     path("inspect/", views.inspect_upload),
]
