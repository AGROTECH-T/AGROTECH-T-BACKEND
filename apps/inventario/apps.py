"""Dominio de inventario por finca.

Propósito: productos con código de barras, stock y movimientos.
Contexto: cada producto pertenece a una finca con membresía.
@author Cristian Deysdayr Jimenez
"""
from django.apps import AppConfig


class InventarioConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.inventario"
    label = "inventario"
    verbose_name = "Inventario"
