"""Serializers HTTP de insumos.

Propósito: validar altas/ediciones con catálogo de categoría y unidad.
@author Cristian Deysdayr Jimenez
"""
from django.db.models import F, Sum
from rest_framework import serializers

from apps.inventario.catalog import plain_amount


class ProductCreateSerializer(serializers.Serializer):
    """Cuerpo para crear un insumo en la finca."""

    name = serializers.CharField(max_length=100, allow_blank=False)
    barcode = serializers.CharField(
        max_length=64,
        required=False,
        allow_blank=True,
        default="",
    )
    unit = serializers.CharField(max_length=40)
    category = serializers.CharField(max_length=40)
    min_stock = serializers.DecimalField(
        max_digits=12,
        decimal_places=3,
        required=False,
        default=0,
    )

    def validate(self, attrs):
        attrs["unit_code"] = attrs.pop("unit")
        attrs["category_code"] = attrs.pop("category")
        return attrs


class ProductUpdateSerializer(serializers.Serializer):
    """Cuerpo parcial para editar un insumo."""

    name = serializers.CharField(max_length=100, required=False)
    barcode = serializers.CharField(max_length=64, required=False, allow_blank=True)
    unit = serializers.CharField(max_length=40, required=False)
    category = serializers.CharField(max_length=40, required=False)
    min_stock = serializers.DecimalField(
        max_digits=12,
        decimal_places=3,
        required=False,
    )
    is_active = serializers.BooleanField(required=False)

    def validate(self, attrs):
        if "unit" in attrs:
            attrs["unit_code"] = attrs.pop("unit")
        if "category" in attrs:
            attrs["category_code"] = attrs.pop("category")
        return attrs


class ProductResponseSerializer(serializers.Serializer):
    """Respuesta pública de un insumo."""

    id = serializers.IntegerField()
    farm_id = serializers.IntegerField()
    name = serializers.CharField()
    barcode = serializers.CharField()
    unit = serializers.SerializerMethodField()
    unit_label = serializers.SerializerMethodField()
    category = serializers.SerializerMethodField()
    category_label = serializers.SerializerMethodField()
    min_stock = serializers.DecimalField(max_digits=12, decimal_places=3)
    quantity = serializers.DecimalField(max_digits=12, decimal_places=3)
    lent_quantity = serializers.SerializerMethodField()
    is_active = serializers.BooleanField()
    is_low_stock = serializers.SerializerMethodField()
    unit_nature = serializers.SerializerMethodField()
    unit_factor = serializers.SerializerMethodField()
    unit_decimal_places = serializers.SerializerMethodField()
    base_symbol = serializers.SerializerMethodField()
    unit_equivalent = serializers.SerializerMethodField()
    has_image = serializers.SerializerMethodField()
    last_movement_at = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()

    def get_unit(self, obj) -> str:
        return obj.unit.code

    def get_unit_label(self, obj) -> str:
        return obj.unit.label

    def get_category(self, obj) -> str:
        return obj.category.code

    def get_category_label(self, obj) -> str:
        return obj.category.name

    def get_lent_quantity(self, obj) -> str:
        value = getattr(obj, "lent_quantity", None)
        if value is None:
            value = obj.loans.aggregate(total=Sum(F("quantity") - F("returned_quantity")))["total"]
        return str(value or 0)

    def get_is_low_stock(self, obj) -> bool:
        return obj.quantity <= obj.min_stock

    def get_unit_nature(self, obj) -> str:
        return obj.unit.nature

    def get_unit_factor(self, obj) -> str:
        return str(obj.unit.factor)

    def get_unit_decimal_places(self, obj) -> int:
        return obj.unit.decimal_places

    def get_base_symbol(self, obj) -> str:
        return obj.unit.base_unit.symbol

    def get_last_movement_at(self, obj):
        if "last_movement_at" in obj.__dict__:
            return obj.__dict__["last_movement_at"]
        return (
            obj.movements.order_by("-created_at")
            .values_list("created_at", flat=True)
            .first()
        )

    def get_has_image(self, obj) -> bool:
        return bool(obj.image)

    def get_unit_equivalent(self, obj) -> str:
        base = obj.unit.base_unit
        factor = obj.unit.factor
        if factor == 1 and obj.unit.code == base.code:
            return base.symbol
        return f"{plain_amount(factor)} {base.symbol}"


class ErrorSerializer(serializers.Serializer):
    """Contrato de error uniforme."""

    error = serializers.CharField()
