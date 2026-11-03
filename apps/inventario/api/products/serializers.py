"""Serializers HTTP de insumos.

Propósito: validar altas con unidad y categoría del catálogo del sistema.
@author Cristian Deysdayr Jimenez
"""
from rest_framework import serializers

from apps.inventario.catalog import InventoryCategory, InventoryUnit


class ProductCreateSerializer(serializers.Serializer):
    """Cuerpo para crear un insumo en la finca."""

    name = serializers.CharField(max_length=100, allow_blank=False)
    barcode = serializers.CharField(max_length=64, allow_blank=False)
    unit = serializers.ChoiceField(choices=InventoryUnit.choices)
    category = serializers.ChoiceField(choices=InventoryCategory.choices)
    min_stock = serializers.DecimalField(
        max_digits=12,
        decimal_places=3,
        required=False,
        default=0,
    )


class ProductResponseSerializer(serializers.Serializer):
    """Respuesta pública de un insumo."""

    id = serializers.IntegerField()
    farm_id = serializers.IntegerField()
    name = serializers.CharField()
    barcode = serializers.CharField()
    unit = serializers.CharField()
    unit_label = serializers.SerializerMethodField()
    category = serializers.CharField()
    category_label = serializers.SerializerMethodField()
    min_stock = serializers.DecimalField(max_digits=12, decimal_places=3)
    quantity = serializers.DecimalField(max_digits=12, decimal_places=3)
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()

    def get_unit_label(self, obj) -> str:
        try:
            return InventoryUnit(obj.unit).label
        except ValueError:
            return obj.unit

    def get_category_label(self, obj) -> str:
        try:
            return InventoryCategory(obj.category).label
        except ValueError:
            return obj.category


class ErrorSerializer(serializers.Serializer):
    """Contrato de error uniforme."""

    error = serializers.CharField()
