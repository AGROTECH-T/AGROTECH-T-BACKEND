"""Permisos de inventario según el rol de membresía y sus permisos directos.

Propósito: traducir cada acción del inventario a un permiso del catálogo.
Contexto: un permiso directo puede sumar una acción que el rol no traía.
@author Cristian Deysdayr Jimenez
"""
from apps.accounts.errors import AppError
from apps.farms import permissions as farm_permissions
from apps.farms.models import FarmMembership

READ = "read"
WRITE_STOCK = "write_stock"
ADJUST = "adjust"
MANAGE_PRODUCT = "manage_product"
CREATE_UNIT = "create_unit"

_ACCION_A_PERMISO = {
    READ: farm_permissions.VER_INVENTARIO,
    WRITE_STOCK: farm_permissions.REGISTRAR_MOVIMIENTOS,
    ADJUST: farm_permissions.AJUSTAR_STOCK,
    MANAGE_PRODUCT: farm_permissions.GESTIONAR_PRODUCTOS,
    CREATE_UNIT: farm_permissions.CREAR_UNIDADES,
}


def require_action(membership: FarmMembership, action: str) -> None:
    """Exige que el rol o un permiso directo permita la acción."""
    permiso = _ACCION_A_PERMISO[action]
    if not farm_permissions.tiene_permiso(membership, permiso):
        raise AppError("no tienes permiso para esta acción", 403)
