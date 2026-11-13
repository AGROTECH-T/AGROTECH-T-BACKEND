"""Módulo de galpón para pollos de engorde.

Sigue cada lote desde la preparación del galpón hasta el cierre.
La postura queda fuera. Las cifras de una finca se configuran, no se imponen.
"""
from django.apps import AppConfig


class AviculturaConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.production.avicultura"
    label = "avicultura"
    verbose_name = "Avicultura"
