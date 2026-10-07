"""Módulo piscicultura (esqueleto, sin lógica)."""
from django.apps import AppConfig


class PisciculturaConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.production.piscicultura"
    label = "piscicultura"
    verbose_name = "Piscicultura"
