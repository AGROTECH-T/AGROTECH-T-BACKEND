"""Permisos efectivos de una membresía.

Propósito: reunir en un solo lugar lo que concede el rol y lo que se suma con
permisos directos, para que la pantalla y la autorización hablen igual.
Contexto: el permiso directo solo suma; nunca quita lo que da el rol.
@author Cristian Deysdayr Jimenez
"""

from apps.farms.models import FarmMembership, FarmRole
from apps.farms.role_permisos_ops import PERMISOS_OPS

VER_INVENTARIO = "ver_inventario"
REGISTRAR_MOVIMIENTOS = "registrar_movimientos"
CREAR_UNIDADES = "crear_unidades"
AJUSTAR_STOCK = "ajustar_stock"
GESTIONAR_PRODUCTOS = "gestionar_productos"
VER_REPORTES = "ver_reportes"
VER_GALPON = "ver_galpon"
REGISTRAR_GALPON = "registrar_galpon"
CERRAR_CICLO = "cerrar_ciclo"

PERMISOS_POR_ROL = {
    FarmRole.CONSULTA: frozenset({VER_INVENTARIO, VER_REPORTES, VER_GALPON}),
    FarmRole.OPERADOR: frozenset(
        {
            VER_INVENTARIO,
            REGISTRAR_MOVIMIENTOS,
            CREAR_UNIDADES,
            VER_REPORTES,
            VER_GALPON,
            REGISTRAR_GALPON,
            CERRAR_CICLO,
        }
    ),
    FarmRole.ADMINISTRADOR: frozenset(
        {
            VER_INVENTARIO,
            REGISTRAR_MOVIMIENTOS,
            CREAR_UNIDADES,
            AJUSTAR_STOCK,
            GESTIONAR_PRODUCTOS,
            VER_REPORTES,
            VER_GALPON,
            REGISTRAR_GALPON,
            CERRAR_CICLO,
        }
    ),
}

# Solo se ofrecen los que un administrador puede sumar: lo que ya trae todo rol
# no se ofrece porque no cambiaría nada.
_IDS_ASIGNABLES = (
    REGISTRAR_MOVIMIENTOS,
    CREAR_UNIDADES,
    AJUSTAR_STOCK,
    GESTIONAR_PRODUCTOS,
    REGISTRAR_GALPON,
    CERRAR_CICLO,
)

PERMISOS_ASIGNABLES = tuple(
    {"id": permiso["id"], "nombre": permiso["nombre"], "modulo": permiso["modulo"]}
    for permiso in PERMISOS_OPS
    if permiso["id"] in _IDS_ASIGNABLES
)

PERMISOS_CATALOGO = tuple(
    {"id": permiso["id"], "nombre": permiso["nombre"], "modulo": permiso["modulo"]}
    for permiso in PERMISOS_OPS
)

NOMBRES_PERMISO = {permiso["id"]: permiso["nombre"] for permiso in PERMISOS_OPS}


def permisos_directos(membership: FarmMembership) -> set[str]:
    """Códigos guardados como permiso extra de la membresía."""
    return set(membership.extra_permissions.values_list("permission", flat=True))


def permisos_efectivos(membership: FarmMembership) -> set[str]:
    """Lo que concede el rol más lo que se sumó a mano."""
    del_rol = PERMISOS_POR_ROL.get(membership.role, frozenset())
    return set(del_rol) | permisos_directos(membership)


def tiene_permiso(membership: FarmMembership, permission: str) -> bool:
    """Indica si la membresía puede ejecutar esa acción."""
    return permission in permisos_efectivos(membership)
