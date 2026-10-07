"""Dominio alertas (esqueleto, sin lógica)."""
from django.apps import AppConfig


class AlertasConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.alertas"
    label = "alertas"
    verbose_name = "Alertas"
