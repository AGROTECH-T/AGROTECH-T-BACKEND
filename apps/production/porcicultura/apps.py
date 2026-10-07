"""Módulo porcicultura (esqueleto, sin lógica)."""
from django.apps import AppConfig


class PorciculturaConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.production.porcicultura"
    label = "porcicultura"
    verbose_name = "Porcicultura"
