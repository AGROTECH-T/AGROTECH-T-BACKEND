"""Serializers HTTP de movimientos.

Propósito: validar entradas/salidas/ajustes y exponer el historial.
@author Cristian Deysdayr Jimenez
"""
from rest_framework import serializers

from apps.inventario.models import MovementKind


class MovementCreateSerializer(serializers.Serializer):
    """Cuerpo para registrar un movimiento de stock."""

    kind = serializers.ChoiceField(choices=MovementKind.choices)
    quantity = serializers.DecimalField(
        max_digits=12,
        decimal_places=3,
        required=False,
        allow_null=True,
    )
    note = serializers.CharField(
        max_length=200,
        required=False,
        allow_blank=True,
        default="",
    )
    take_all = serializers.BooleanField(required=False, default=False)
    idempotency_key = serializers.CharField(
        max_length=64,
        required=False,
        allow_blank=True,
        default="",
    )


class MovementResponseSerializer(serializers.Serializer):
    """Respuesta pública de un movimiento."""

    id = serializers.IntegerField()
    kind = serializers.CharField()
    quantity = serializers.DecimalField(max_digits=12, decimal_places=3)
    quantity_base = serializers.DecimalField(max_digits=16, decimal_places=6)
    stock_before = serializers.DecimalField(max_digits=12, decimal_places=3)
    stock_after = serializers.DecimalField(max_digits=12, decimal_places=3)
    unit = serializers.SerializerMethodField()
    unit_label = serializers.SerializerMethodField()
    base_symbol = serializers.SerializerMethodField()
    note = serializers.CharField()
    product_id = serializers.IntegerField()
    product_name = serializers.SerializerMethodField()
    created_by_id = serializers.IntegerField()
    created_by_name = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField()

    def get_unit(self, obj) -> str:
        return obj.unit.code

    def get_unit_label(self, obj) -> str:
        return obj.unit.label

    def get_base_symbol(self, obj) -> str:
        return obj.unit.base_unit.symbol

    def get_product_name(self, obj) -> str:
        return obj.product.name

    def get_created_by_name(self, obj) -> str:
        return f"{obj.created_by.first_name} {obj.created_by.last_name}".strip()
