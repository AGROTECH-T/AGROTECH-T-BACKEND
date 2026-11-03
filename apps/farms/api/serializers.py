"""Contratos de salida del catálogo de roles y permisos.

Propósito: documentar en OpenAPI la forma del catálogo funcional.
Contexto: los datos provienen de apps.farms.roles y no se persisten.
"""

from rest_framework import serializers


class RolSerializer(serializers.Serializer):
    """Describe un rol o un actor del sistema."""

    id = serializers.CharField()
    nombre = serializers.CharField()
    resumen = serializers.CharField()
    funciones = serializers.ListField(child=serializers.CharField())


class CeldaSerializer(serializers.Serializer):
    """Describe el permiso de un rol para una acción."""

    estado = serializers.CharField()
    detalle = serializers.CharField(allow_blank=True)


class PermisoSerializer(serializers.Serializer):
    """Agrupa el permiso de cada rol para una acción."""

    id = serializers.CharField()
    nombre = serializers.CharField()
    por_rol = serializers.DictField(child=CeldaSerializer())


class ReglaSerializer(serializers.Serializer):
    """Describe una regla transversal de autorización."""

    numero = serializers.IntegerField()
    titulo = serializers.CharField()
    detalle = serializers.CharField()


class RolesPermisosSerializer(serializers.Serializer):
    """Documenta el catálogo completo que consume Configuración."""

    roles = RolSerializer(many=True)
    actores = RolSerializer(many=True)
    permisos = PermisoSerializer(many=True)
    reglas = ReglaSerializer(many=True)
    pendientes = serializers.ListField(child=serializers.CharField())
    estados = serializers.ListField(child=serializers.CharField())
