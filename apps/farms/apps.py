"""Dominio de fincas y membresías.

Propósito: representar fincas y la relación de cada cuenta con su rol.
Contexto: v1 expone listado, creación y consulta; la autorización por rol queda
para una fase posterior.
"""
from django.apps import AppConfig


class FarmsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.farms"
    label = "farms"
    verbose_name = "Farms"
