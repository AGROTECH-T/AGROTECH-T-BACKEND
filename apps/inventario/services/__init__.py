"""Casos de uso del inventario.

Propósito: reexportar operaciones de productos y movimientos.
Contexto: la lógica vive en módulos por recurso.
@author Cristian Deysdayr Jimenez
"""
from apps.inventario.services.movements import list_movements, register_movement
from apps.inventario.services.products import create_product, get_product, list_products

__all__ = [
    "create_product",
    "get_product",
    "list_movements",
    "list_products",
    "register_movement",
]
