"""Casos de uso de movimientos de stock.

Propósito: registrar entradas, salidas y ajustes; listar historial.
Contexto: actualiza el stock del producto en la misma transacción.
@author Cristian Deysdayr Jimenez
"""
from decimal import Decimal

from django.db import transaction

from apps.accounts.errors import AppError
from apps.accounts.models import User
from apps.inventario.models import Movement, MovementKind, Product
from apps.inventario.services.membership import require_write_membership
from apps.inventario.services.products import get_product

ZERO = Decimal("0")


def register_movement(
    *,
    user: User,
    farm_id: int,
    product_id: int,
    kind: str,
    quantity: Decimal,
    note: str = "",
) -> Movement:
    """Aplica un movimiento tras bloquear finca, membresía y producto."""
    if kind not in MovementKind.values:
        raise AppError("tipo de movimiento inválido", 400)
    if quantity <= ZERO:
        raise AppError("la cantidad debe ser mayor que cero", 400)

    with transaction.atomic():
        require_write_membership(user=user, farm_id=farm_id)
        product = (
            Product.objects.select_for_update()
            .filter(id=product_id, farm_id=farm_id, is_active=True)
            .first()
        )
        if product is None:
            raise AppError("producto no encontrado", 404)

        new_qty = _next_quantity(product.quantity, kind, quantity)
        if new_qty < ZERO:
            raise AppError("no hay stock suficiente para la salida", 400)

        product.quantity = new_qty
        product.save(update_fields=["quantity", "updated_at"])
        return Movement.objects.create(
            product=product,
            kind=kind,
            quantity=quantity,
            note=note.strip()[:200],
            created_by=user,
        )


def list_movements(*, user: User, farm_id: int, product_id: int):
    """Lista movimientos recientes del producto."""
    product = get_product(user=user, farm_id=farm_id, product_id=product_id)
    return product.movements.select_related("created_by")[:50]


def _next_quantity(current: Decimal, kind: str, amount: Decimal) -> Decimal:
    """Calcula el stock resultante según el tipo de movimiento."""
    if kind == MovementKind.ENTRADA:
        return current + amount
    if kind == MovementKind.SALIDA:
        return current - amount
    return amount
