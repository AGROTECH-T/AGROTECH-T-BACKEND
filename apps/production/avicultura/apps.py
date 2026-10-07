"""Módulo avicultura (esqueleto, sin lógica).

Primer módulo productivo a desarrollar en una fase posterior
(AVICULTURA / GALPÓN). En esta fase solo se crea el esqueleto.
No debe modificar estructuras internas de otros dominios: comunicarse
únicamente vía interfaces/servicios definidos.
"""
from django.apps import AppConfig


class AviculturaConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.production.avicultura"
    label = "avicultura"
    verbose_name = "Avicultura"
