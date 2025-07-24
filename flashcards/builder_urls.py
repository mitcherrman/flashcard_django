from django.urls import path
from . import builder_views

urlpatterns = [
    path("generate/", builder_views.generate_deck, name="generate_deck"),
]
