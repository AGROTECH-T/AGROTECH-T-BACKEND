"""Módulo hidroponia (esqueleto, sin lógica)."""
from django.apps import AppConfig


class HidroponiaConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.production.hidroponia"
    label = "hidroponia"
    verbose_name = "Hidroponia"
