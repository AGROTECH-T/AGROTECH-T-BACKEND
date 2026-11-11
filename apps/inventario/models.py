"""Modelos de inventario: catálogo, productos y movimientos.

Propósito: despensa por finca con unidades equivalentes e historial inmutable.
Contexto: categoría/unidad del catálogo; stock solo cambia por movimientos.
@author Cristian Deysdayr Jimenez
"""
from django.db import models
from django.db.models import Q

from apps.accounts.models import User
from apps.farms.models import Farm, FarmMembership


class MagnitudeNature(models.TextChoices):
    """Cómo se pide la cantidad según la unidad."""

    DISCRETA = "discreta", "Discreta"
    CONTINUA = "continua", "Continua"
    PRESENTACION = "presentacion", "Presentación"


class MovementKind(models.TextChoices):
    """Tipos de movimiento de stock."""

    ENTRADA = "entrada", "Entrada"
    SALIDA = "salida", "Salida"
    AJUSTE = "ajuste", "Ajuste"


class InventoryCategory(models.Model):
    """Categoría cerrada del catálogo (el productor no crea categorías)."""

    code = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=80)
    description = models.CharField(max_length=200, blank=True, default="")
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "inventory_categories"
        ordering = ["name", "id"]

    def __str__(self) -> str:
        return self.name


class InventoryBaseUnit(models.Model):
    """Unidad base de magnitud (kg, g, L, u, m…)."""

    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=60)
    symbol = models.CharField(max_length=10)
    nature = models.CharField(max_length=20, choices=MagnitudeNature.choices)
    decimal_places = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = "inventory_base_units"
        ordering = ["code"]

    def __str__(self) -> str:
        return self.symbol


class InventoryUnit(models.Model):
    """Unidad visible del desplegable (base o presentación comercial)."""

    code = models.CharField(max_length=40, unique=True)
    label = models.CharField(max_length=80)
    base_unit = models.ForeignKey(
        InventoryBaseUnit,
        on_delete=models.PROTECT,
        related_name="presentations",
    )
    factor = models.DecimalField(max_digits=14, decimal_places=6, default=1)
    nature = models.CharField(max_length=20, choices=MagnitudeNature.choices)
    decimal_places = models.PositiveSmallIntegerField(default=0)
    is_system = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inventory_units_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "inventory_units"
        ordering = ["label", "id"]

    def __str__(self) -> str:
        return self.label


class CategoryUnit(models.Model):
    """Qué unidades aplican a cada categoría."""

    category = models.ForeignKey(
        InventoryCategory,
        on_delete=models.CASCADE,
        related_name="unit_links",
    )
    unit = models.ForeignKey(
        InventoryUnit,
        on_delete=models.CASCADE,
        related_name="category_links",
    )

    class Meta:
        db_table = "inventory_category_units"
        constraints = [
            models.UniqueConstraint(
                fields=("category", "unit"),
                name="uniq_inventory_category_unit",
            ),
        ]


class Product(models.Model):
    """Insumo de inventario asociado a una finca."""

    farm = models.ForeignKey(Farm, on_delete=models.CASCADE, related_name="products")
    name = models.CharField(max_length=100)
    barcode = models.CharField(max_length=64, blank=True, default="")
    category = models.ForeignKey(
        InventoryCategory,
        on_delete=models.PROTECT,
        related_name="products",
    )
    unit = models.ForeignKey(
        InventoryUnit,
        on_delete=models.PROTECT,
        related_name="products",
    )
    min_stock = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    quantity = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    is_active = models.BooleanField(default=True)
    is_removed = models.BooleanField(default=False)
    image = models.FileField(upload_to="products", blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "inventory_products"
        ordering = ["name", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=("farm", "barcode"),
                condition=~Q(barcode=""),
                name="uniq_inventory_barcode_per_farm",
            ),
        ]

    @property
    def is_low_stock(self) -> bool:
        """Indicador visible: cantidad en o bajo el mínimo."""
        return self.quantity <= self.min_stock


class ProductLoan(models.Model):
    """Cantidad de un producto prestada a una persona."""

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="loans",
    )
    borrower_name = models.CharField(max_length=120)
    document_number = models.CharField(max_length=32)
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    returned_quantity = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    created_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="product_loans",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "inventory_product_loans"
        ordering = ["-created_at", "-id"]


class Movement(models.Model):
    """Entrada, salida o ajuste sobre un insumo (inmutable)."""

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="movements",
    )
    kind = models.CharField(max_length=20, choices=MovementKind.choices)
    quantity = models.DecimalField(max_digits=12, decimal_places=3)
    unit = models.ForeignKey(
        InventoryUnit,
        on_delete=models.PROTECT,
        related_name="movements",
    )
    quantity_base = models.DecimalField(max_digits=16, decimal_places=6)
    stock_before = models.DecimalField(max_digits=12, decimal_places=3)
    stock_after = models.DecimalField(max_digits=12, decimal_places=3)
    note = models.CharField(max_length=200, blank=True, default="")
    created_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="inventory_movements",
    )
    membership = models.ForeignKey(
        FarmMembership,
        on_delete=models.PROTECT,
        related_name="inventory_movements",
    )
    idempotency_key = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "inventory_movements"
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=("product", "idempotency_key"),
                condition=~Q(idempotency_key=""),
                name="uniq_inventory_movement_idempotency",
            ),
        ]
