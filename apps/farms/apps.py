"""Dominio farms (esqueleto, sin lógica).

Dominio transversal: fincas, membresías y relaciones usuario-finca.
Sin reglas de negocio definidas todavía: no implementar.
"""
from django.apps import AppConfig


class FarmsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.farms"
    label = "farms"
    verbose_name = "Farms"
