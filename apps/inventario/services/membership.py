"""Acceso a finca para operar inventario.

Propósito: exigir membresía antes de tocar productos o stock.
@author Cristian Deysdayr Jimenez
"""
from apps.accounts.errors import AppError
from apps.accounts.models import User
from apps.farms import services as farm_services


def require_membership(*, user: User, farm_id: int):
    """Exige membresía en la finca o responde 404."""
    membership = farm_services.get_membership(user=user, farm_id=farm_id)
    if membership is None:
        raise AppError("finca no encontrada", 404)
    return membership
