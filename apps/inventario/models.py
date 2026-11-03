"""Modelos de inventario.

Propósito: insumos por finca con unidad y categoría del catálogo del sistema.
Contexto: código de barras obligatorio y único dentro de la finca.
@author Cristian Deysdayr Jimenez
"""
from django.db import models

from apps.accounts.models import User
from apps.farms.models import Farm
from apps.inventario.catalog import InventoryCategory, InventoryUnit


class MovementKind(models.TextChoices):
    """Tipos de movimiento de stock."""

    ENTRADA = "entrada", "Entrada"
    SALIDA = "salida", "Salida"
    AJUSTE = "ajuste", "Ajuste"


class Product(models.Model):
    """Insumo de inventario asociado a una finca."""

    farm = models.ForeignKey(Farm, on_delete=models.CASCADE, related_name="products")
    name = models.CharField(max_length=100)
    barcode = models.CharField(max_length=64)
    unit = models.CharField(max_length=20, choices=InventoryUnit.choices)
    category = models.CharField(
        max_length=40,
        choices=InventoryCategory.choices,
        default=InventoryCategory.OTROS,
    )
    min_stock = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    quantity = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "inventory_products"
        ordering = ["name", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=("farm", "barcode"),
                name="uniq_inventory_barcode_per_farm",
            ),
        ]


class Movement(models.Model):
    """Entrada, salida o ajuste sobre un insumo."""

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="movements",
    )
    kind = models.CharField(max_length=20, choices=MovementKind.choices)
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    note = models.CharField(max_length=200, blank=True, default="")
    created_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="inventory_movements",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "inventory_movements"
        ordering = ["-created_at", "-id"]
