"""Dominio inventario (esqueleto, sin lógica).

Responsabilidad futura (responsable: Cristian). No implementar en esta fase.
Otros dominios NO deben modificar sus estructuras internas directamente:
solo vía interfaces/servicios definidos.
"""
from django.apps import AppConfig


class InventarioConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.inventario"
    label = "inventario"
    verbose_name = "Inventario"
