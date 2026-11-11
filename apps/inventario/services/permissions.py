"""Permisos de inventario según el rol de membresía.

Propósito: reutilizar roles de farms sin inventar roles nuevos.
@author Cristian Deysdayr Jimenez
"""
from apps.accounts.errors import AppError
from apps.farms.models import FarmMembership, FarmRole

READ = "read"
WRITE_STOCK = "write_stock"
ADJUST = "adjust"
MANAGE_PRODUCT = "manage_product"
CREATE_UNIT = "create_unit"

_ROLE_ACTIONS = {
    FarmRole.CONSULTA: {READ},
    FarmRole.OPERADOR: {READ, WRITE_STOCK, CREATE_UNIT},
    FarmRole.ADMINISTRADOR: {
        READ,
        WRITE_STOCK,
        ADJUST,
        MANAGE_PRODUCT,
        CREATE_UNIT,
    },
}


def require_action(membership: FarmMembership, action: str) -> None:
    """Exige que el rol de la membresía permita la acción."""
    allowed = _ROLE_ACTIONS.get(membership.role, set())
    if action not in allowed:
        raise AppError("no tienes permiso para esta acción", 403)
