"""Dominio trazabilidad (esqueleto, sin lógica).

Trazabilidad y posterior integración con QR. No implementar en esta fase.
"""
from django.apps import AppConfig


class TrazabilidadConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.trazabilidad"
    label = "trazabilidad"
    verbose_name = "Trazabilidad"
