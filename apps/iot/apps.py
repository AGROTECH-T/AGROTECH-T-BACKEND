"""Dominio iot (esqueleto, sin lógica).

Integración futura con sensores y dispositivos. No implementar en esta fase.
"""
from django.apps import AppConfig


class IotConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.iot"
    label = "iot"
    verbose_name = "IoT"
