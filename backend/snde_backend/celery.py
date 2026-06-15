"""Configuration Celery pour le projet SNDE."""
import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "snde_backend.settings")

app = Celery("snde_backend")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
