"""Serializers de unidades custom.

@author Cristian Deysdayr Jimenez
"""
from rest_framework import serializers


class CustomUnitCreateSerializer(serializers.Serializer):
    """Cuerpo para crear una presentación que falta en el catálogo."""

    stem = serializers.CharField(max_length=30)
    label = serializers.CharField(max_length=80)
    base_unit = serializers.CharField(max_length=20)
    factor = serializers.DecimalField(max_digits=14, decimal_places=6)
    categories = serializers.ListField(
        child=serializers.CharField(max_length=40),
        allow_empty=False,
    )
    nature = serializers.CharField(max_length=20, required=False)

    def validate(self, attrs):
        attrs["base_unit_code"] = attrs.pop("base_unit")
        attrs["category_codes"] = attrs.pop("categories")
        return attrs


class UnitResponseSerializer(serializers.Serializer):
    """Unidad creada o del catálogo."""

    id = serializers.CharField(source="code")
    label = serializers.CharField()
    nature = serializers.CharField()
    decimal_places = serializers.IntegerField()
    factor = serializers.DecimalField(max_digits=14, decimal_places=6)
    base_unit = serializers.SerializerMethodField()
    is_system = serializers.BooleanField()

    def get_base_unit(self, obj) -> str:
        return obj.base_unit.code
