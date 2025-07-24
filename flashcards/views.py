# flashcards/views.py   (optional HTML views or landing page)
from django.views.generic import TemplateView
class Landing(TemplateView):
    template_name = "landing.html"
