# flashcards/urls.py
from django.urls import path
from . import views
from . import views_async  # new async endpoints

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
    path("toc/",      views.toc,           name="toc"),

    path("template/build/", views_async.template_build),
    path("template/status/<str:task_id>/", views_async.template_status),
    path("template/check/", views_async.template_check),

    # Deck build async
    path("build/deck/", views_async.start_build_deck),
    path("build/deck/status/<str:job_id>/", views_async.build_deck_status),
    
]
