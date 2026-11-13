"""Catálogo funcional de roles y permisos del módulo Farms.

Propósito: reexportar el catálogo y armar la respuesta de Configuración.
Contexto: no persiste membresías ni valida fincas.
"""

from apps.farms.permissions import PERMISOS_ASIGNABLES
from apps.farms.role_actors import (
    ACTORES,
    ADMINISTRADOR,
    CONDICIONADO,
    CONSULTA,
    ESTADOS,
    NO_PERMITIDO,
    NO_REQUIERE,
    OPERADOR,
    PENDIENTE,
    PERMITIDO,
    ROLES,
)
from apps.farms.role_permisos import PERMISOS
from apps.farms.role_rules import PENDIENTES, REGLAS

__all__ = [
    "ACTORES",
    "ADMINISTRADOR",
    "CONDICIONADO",
    "CONSULTA",
    "ESTADOS",
    "NO_PERMITIDO",
    "NO_REQUIERE",
    "OPERADOR",
    "PENDIENTE",
    "PENDIENTES",
    "PERMISOS",
    "PERMISOS_ASIGNABLES",
    "PERMITIDO",
    "REGLAS",
    "ROLES",
    "catalogo",
]


def catalogo() -> dict:
    """Arma el catálogo completo de roles, actores, matriz y reglas.

    @returns Diccionario listo para serializar en la API.
    """
    return {
        "roles": ROLES,
        "actores": ACTORES,
        "permisos": PERMISOS,
        "asignables": PERMISOS_ASIGNABLES,
        "reglas": REGLAS,
        "pendientes": PENDIENTES,
        "estados": ESTADOS,
    }
