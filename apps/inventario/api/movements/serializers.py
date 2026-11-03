"""Serializers HTTP de movimientos.

Propósito: validar entradas/salidas/ajustes y exponer el historial.
Contexto: se usan bajo el detalle de un producto.
@author Cristian Deysdayr Jimenez
"""
from rest_framework import serializers

from apps.inventario.models import MovementKind


class MovementCreateSerializer(serializers.Serializer):
    """Cuerpo para registrar un movimiento de stock."""

    kind = serializers.ChoiceField(choices=MovementKind.choices)
    quantity = serializers.DecimalField(max_digits=12, decimal_places=3)
    note = serializers.CharField(
        max_length=200,
        required=False,
        allow_blank=True,
        default="",
    )


class MovementResponseSerializer(serializers.Serializer):
    """Respuesta pública de un movimiento."""

    id = serializers.IntegerField()
    kind = serializers.CharField()
    quantity = serializers.DecimalField(max_digits=12, decimal_places=3)
    note = serializers.CharField()
    created_at = serializers.DateTimeField()
