"""Contratos del catálogo de roles y de la asignación de permisos.

Propósito: documentar en OpenAPI la forma del catálogo y de las personas.
Contexto: los datos provienen de apps.farms.roles y apps.farms.asignacion.
@author Cristian Deysdayr Jimenez
"""
from rest_framework import serializers
from apps.accounts.validators import (
    normalize_identification,
    normalize_name,
    normalize_phone,
    validate_password_strength,
)
from apps.farms.models import FarmRole
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
    modulo = serializers.CharField()
    por_rol = serializers.DictField(child=CeldaSerializer())
class PermisoAsignableSerializer(serializers.Serializer):
    """Permiso que se puede sumar a una membresía."""

    id = serializers.CharField()
    nombre = serializers.CharField()
    modulo = serializers.CharField()
class ReglaSerializer(serializers.Serializer):
    """Describe una regla transversal de autorización."""

    numero = serializers.IntegerField()
    titulo = serializers.CharField()
    detalle = serializers.CharField()
class MembresiaAsignadaSerializer(serializers.Serializer):
    """Rol de una persona dentro de una finca y sus permisos."""

    farm_id = serializers.IntegerField()
    farm_name = serializers.CharField()
    role = serializers.CharField()
    permisos_efectivos = PermisoAsignableSerializer(many=True, required=False)
    permisos_directos = PermisoAsignableSerializer(many=True, required=False)
class UsuarioAsignadoSerializer(serializers.Serializer):
    """Persona visible para quien administra sus fincas."""

    id = serializers.IntegerField()
    nombre = serializers.CharField()
    identification = serializers.CharField()
    correo = serializers.CharField(allow_blank=True)
    activo = serializers.BooleanField()
    es_yo = serializers.BooleanField()
    membresias = MembresiaAsignadaSerializer(many=True)
class AsignacionSerializer(serializers.Serializer):
    """Listado de personas y si el usuario puede cambiar roles."""

    puedo_asignar = serializers.BooleanField()
    fincas = serializers.ListField(child=serializers.DictField())
    usuarios = UsuarioAsignadoSerializer(many=True)
class CambiarRolSerializer(serializers.Serializer):
    """Rol nuevo de una persona en una finca."""

    farm_id = serializers.IntegerField(min_value=1)
    role = serializers.ChoiceField(choices=FarmRole.values)
class RolesPermisosSerializer(serializers.Serializer):
    """Documenta el catálogo completo que consume Configuración."""

    roles = RolSerializer(many=True)
    actores = RolSerializer(many=True)
    permisos = PermisoSerializer(many=True)
    asignables = PermisoAsignableSerializer(many=True)
    reglas = ReglaSerializer(many=True)
    pendientes = serializers.ListField(child=serializers.CharField())
    estados = serializers.ListField(child=serializers.CharField())
