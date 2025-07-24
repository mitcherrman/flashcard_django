from django.urls import path
from . import api_views

urlpatterns = [
    path("hand/",     api_views.hand,     name="hand"),
    path("feedback/", api_views.feedback, name="feedback"),
]
