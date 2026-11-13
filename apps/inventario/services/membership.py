"""Acceso a finca para operar inventario.

Propósito: exigir membresía activa y rol suficiente antes de tocar inventario.
@author Cristian Deysdayr Jimenez
"""
from apps.accounts.errors import AppError
from apps.accounts.models import User
from apps.farms import services as farm_services
from apps.farms.capabilities import INVENTORY_WRITE_ROLES

# Fuente única compartida con las capacidades expuestas a la interfaz.
WRITE_ROLES = INVENTORY_WRITE_ROLES


def require_membership(*, user: User, farm_id: int):
    """Exige membresía en una finca activa para operaciones de lectura."""
    return farm_services.require_active_membership(user=user, farm_id=farm_id)


def require_write_membership(*, user: User, farm_id: int):
    """Bloquea la finca y exige un rol con permiso de escritura.

    Debe invocarse dentro de ``transaction.atomic()``. El bloqueo de la finca
    impide que una desactivación concurrente confirme entre la comprobación de
    estado y la escritura de inventario.
    """
    membership = farm_services.lock_active_membership(user=user, farm_id=farm_id)
    if membership.role not in WRITE_ROLES:
        raise AppError("no tienes permiso para modificar el inventario", 403)
    return membership
