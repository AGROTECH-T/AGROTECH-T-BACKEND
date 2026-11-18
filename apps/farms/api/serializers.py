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


class PermisoDirectoSerializer(serializers.Serializer):
    """Permiso que se suma o se retira de una membresía."""

    farm_id = serializers.IntegerField(min_value=1)
    permission = serializers.CharField(max_length=40)


class EstadoUsuarioSerializer(serializers.Serializer):
    """Estado nuevo de la cuenta."""

    activo = serializers.BooleanField()


class CrearUsuarioSerializer(serializers.Serializer):
    """Datos básicos de la cuenta nueva, su rol y sus permisos extra."""

    first_name = serializers.CharField(max_length=40)
    last_name = serializers.CharField(max_length=40)
    identification = serializers.CharField(max_length=30)
    correo = serializers.EmailField()
    phone = serializers.CharField(max_length=30, required=False, allow_blank=True)
    password = serializers.CharField(write_only=True, trim_whitespace=False)
    farm_id = serializers.IntegerField(min_value=1, required=False)
    farm_ids = serializers.ListField(child=serializers.IntegerField(min_value=1), required=False)
    role = serializers.ChoiceField(choices=FarmRole.values)
    permisos = serializers.ListField(child=serializers.CharField(max_length=40), required=False)

    def validate(self, attrs: dict) -> dict:
        """Normaliza los datos y exige una contraseña fuerte."""
        attrs["first_name"] = normalize_name(attrs["first_name"], "primer nombre")
        attrs["last_name"] = normalize_name(attrs["last_name"], "primer apellido")
        attrs["identification"] = normalize_identification(attrs["identification"])
        attrs["correo"] = attrs["correo"].strip().lower()
        attrs["phone"] = normalize_phone(attrs.get("phone") or "") if attrs.get("phone") else ""
        attrs["password"] = validate_password_strength(attrs["password"])
        ids = attrs.get("farm_ids") or ([attrs["farm_id"]] if attrs.get("farm_id") else [])
        if not ids:
            raise serializers.ValidationError("elige al menos una finca")
        attrs["farm_ids"] = list(dict.fromkeys(ids))
        return attrs


class RolesPermisosSerializer(serializers.Serializer):
    """Documenta el catálogo completo que consume Configuración."""

    roles = RolSerializer(many=True)
    actores = RolSerializer(many=True)
    permisos = PermisoSerializer(many=True)
    asignables = PermisoAsignableSerializer(many=True)
    reglas = ReglaSerializer(many=True)
    pendientes = serializers.ListField(child=serializers.CharField())
    estados = serializers.ListField(child=serializers.CharField())
