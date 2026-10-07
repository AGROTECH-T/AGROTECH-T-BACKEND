"""Módulo agricultura (esqueleto, sin lógica)."""
from django.apps import AppConfig


class AgriculturaConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.production.agricultura"
    label = "agricultura"
    verbose_name = "Agricultura"
