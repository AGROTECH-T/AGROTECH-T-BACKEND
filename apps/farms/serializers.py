"""Contratos HTTP del dominio de fincas.

Propósito: validar la creación y documentar la respuesta pública de una finca.
Contexto: el rol se expone con su valor interno del catálogo FarmRole.
"""
from rest_framework import serializers

from apps.farms.models import FarmRole


class FarmCreateSerializer(serializers.Serializer):
    """Valida el único dato editable al crear una finca."""

    name = serializers.CharField(max_length=100, allow_blank=False)

    def to_internal_value(self, data):
        """Rechaza los campos que no pertenecen al contrato de creación."""
        if hasattr(data, "keys"):
            extra_fields = sorted(set(data.keys()) - set(self.fields))
            if extra_fields:
                raise serializers.ValidationError(
                    {field: "Campo no permitido." for field in extra_fields}
                )
        return super().to_internal_value(data)


class FarmResponseSerializer(serializers.Serializer):
    """Expone la finca y el rol del usuario autenticado en ella."""

    id = serializers.IntegerField(source="farm_id")
    name = serializers.CharField(source="farm.name")
    role = serializers.ChoiceField(choices=FarmRole.choices)
    created_at = serializers.DateTimeField(source="farm.created_at")
    updated_at = serializers.DateTimeField(source="farm.updated_at")
