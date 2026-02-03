# flashsite/celery.py
import os
from celery import Celery

# Point Django settings for Celery
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "flashsite.settings")

app = Celery("flashsite")

# Read CELERY_* from Django settings, keys prefixed with "CELERY_"
app.config_from_object("django.conf:settings", namespace="CELERY")

# Auto-discover tasks.py across installed apps (e.g., flashcards.tasks)
app.autodiscover_tasks()

# Optional: a simple ping task for health checks
@app.task(bind=True)
def ping(self):
    return {"ok": True}
