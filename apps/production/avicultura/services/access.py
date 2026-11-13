"""Acceso del módulo de galpón.

Propósito: exigir membresía y separar consulta, registro y cierre.
Contexto: el rol define la base y los permisos directos pueden sumar.
@author Cristian Deysdayr Jimenez
"""
from apps.accounts.errors import AppError
from apps.accounts.models import User
from apps.farms import permissions as farm_permissions
from apps.farms import services as farm_services


def require_membership(*, user: User, farm_id: int):
    """Exige membresía con permiso de consulta del galpón, o responde 404/403."""
    membership = farm_services.get_membership(user=user, farm_id=farm_id)
    if membership is None:
        raise AppError("finca no encontrada", 404)
    if not farm_permissions.tiene_permiso(membership, farm_permissions.VER_GALPON):
        raise AppError("no tienes permiso para esta acción", 403)
    return membership


def require_write(membership) -> None:
    """La consulta puede ver; quien registra necesita el permiso."""
    if not farm_permissions.tiene_permiso(membership, farm_permissions.REGISTRAR_GALPON):
        raise AppError("no tienes permiso para esta acción", 403)


def require_close(membership) -> None:
    """Cerrar el lote o un aviso es aparte del registro diario."""
    if not farm_permissions.tiene_permiso(membership, farm_permissions.CERRAR_CICLO):
        raise AppError("no tienes permiso para esta acción", 403)
