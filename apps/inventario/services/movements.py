"""Casos de uso de movimientos de stock.

Propósito: registrar entradas, salidas y ajustes; listar historial.
Contexto: actualiza el stock del producto en la misma transacción atómica.
@author Cristian Deysdayr Jimenez
"""
from datetime import date
from decimal import ROUND_DOWN, Decimal

from django.db import IntegrityError, transaction
from django.db.models import QuerySet

from apps.accounts.errors import AppError
from apps.accounts.models import User
from apps.inventario.models import MagnitudeNature, Movement, MovementKind, Product
from apps.inventario.services.membership import require_membership
from apps.inventario.services.permissions import ADJUST, READ, WRITE_STOCK, require_action

ZERO = Decimal("0")


def register_movement(
    *,
    user: User,
    farm_id: int,
    product_id: int,
    kind: str,
    quantity: Decimal | None = None,
    note: str = "",
    take_all: bool = False,
    idempotency_key: str = "",
) -> Movement:
    """Aplica un movimiento y actualiza el stock del producto."""
    if kind not in MovementKind.values:
        raise AppError("tipo de movimiento inválido", 400)

    membership = require_membership(user=user, farm_id=farm_id)
    if kind == MovementKind.AJUSTE:
        require_action(membership, ADJUST)
    else:
        require_action(membership, WRITE_STOCK)

    clean_key = idempotency_key.strip()[:64]
    if clean_key:
        existing = Movement.objects.filter(
            product_id=product_id,
            idempotency_key=clean_key,
        ).first()
        if existing is not None:
            return existing

    with transaction.atomic():
        product = (
            Product.objects.select_for_update()
            .select_related("unit", "unit__base_unit")
            .filter(id=product_id, farm_id=farm_id)
            .first()
        )
        if product is None:
            raise AppError("producto no encontrado", 404)
        if not product.is_active:
            raise AppError("producto inactivo: no admite movimientos", 400)

        amount = _resolve_amount(
            product=product,
            kind=kind,
            quantity=quantity,
            take_all=take_all,
        )
        stock_before = product.quantity
        stock_after = _next_quantity(stock_before, kind, amount)
        if stock_after < ZERO:
            raise AppError(
                f"disponible {stock_before}, solicitado {amount}",
                400,
            )

        clean_note = note.strip()[:200]
        if kind == MovementKind.AJUSTE and not clean_note:
            raise AppError("el ajuste requiere una nota obligatoria", 400)
        if take_all and kind == MovementKind.SALIDA and not clean_note:
            clean_note = "Salida total"

        product.quantity = stock_after
        product.save(update_fields=["quantity", "updated_at"])
        quantity_base = (amount * product.unit.factor).quantize(
            Decimal("0.000001"),
            rounding=ROUND_DOWN,
        )
        try:
            return Movement.objects.create(
                product=product,
                kind=kind,
                quantity=amount,
                unit=product.unit,
                quantity_base=quantity_base,
                stock_before=stock_before,
                stock_after=stock_after,
                note=clean_note,
                created_by=user,
                membership=membership,
                idempotency_key=clean_key,
            )
        except IntegrityError:
            existing = Movement.objects.filter(
                product_id=product_id,
                idempotency_key=clean_key,
            ).first()
            if existing is not None:
                return existing
            raise


def list_movements(
    *,
    user: User,
    farm_id: int,
    product_id: int,
) -> QuerySet[Movement]:
    """Lista movimientos del producto."""
    membership = require_membership(user=user, farm_id=farm_id)
    require_action(membership, READ)
    product = Product.objects.filter(id=product_id, farm_id=farm_id).first()
    if product is None:
        raise AppError("producto no encontrado", 404)
    return product.movements.select_related(
        "product",
        "created_by",
        "unit",
        "unit__base_unit",
        "membership",
    )


def list_farm_movements(
    *,
    user: User,
    farm_id: int,
    kind: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> QuerySet[Movement]:
    """Lista movimientos de toda la finca."""
    membership = require_membership(user=user, farm_id=farm_id)
    require_action(membership, READ)
    if date_from is not None and date_to is not None and date_from > date_to:
        raise AppError("el rango de fechas no es válido", 400)
    queryset = Movement.objects.filter(product__farm_id=farm_id).select_related(
        "product",
        "created_by",
        "unit",
        "unit__base_unit",
    )
    if kind:
        if kind not in MovementKind.values:
            raise AppError("tipo de movimiento inválido", 400)
        queryset = queryset.filter(kind=kind)
    if date_from is not None:
        queryset = queryset.filter(created_at__date__gte=date_from)
    if date_to is not None:
        queryset = queryset.filter(created_at__date__lte=date_to)
    return queryset


def _resolve_amount(
    *,
    product: Product,
    kind: str,
    quantity: Decimal | None,
    take_all: bool,
) -> Decimal:
    """Resuelve la cantidad según take_all o el valor enviado."""
    if take_all:
        if kind != MovementKind.SALIDA:
            raise AppError("sacar todo solo aplica a salidas", 400)
        if product.quantity <= ZERO:
            raise AppError("no hay stock para sacar todo", 400)
        return product.quantity

    if quantity is None:
        raise AppError("la cantidad es obligatoria", 400)
    if kind == MovementKind.AJUSTE:
        if quantity < ZERO:
            raise AppError("la cantidad contada no puede ser negativa", 400)
    elif quantity <= ZERO:
        raise AppError("la cantidad debe ser mayor que cero", 400)

    nature = product.unit.nature
    if nature == MagnitudeNature.DISCRETA:
        if quantity != quantity.to_integral_value():
            raise AppError("esta unidad solo admite cantidades enteras", 400)
        if kind != MovementKind.AJUSTE and quantity < 1:
            raise AppError("la cantidad debe ser un entero mayor o igual a 1", 400)
    elif nature == MagnitudeNature.PRESENTACION:
        if quantity != quantity.quantize(Decimal("0.1")):
            raise AppError("esta unidad admite como máximo 1 decimal", 400)
    else:
        places = product.unit.decimal_places
        quant = Decimal("1").scaleb(-places) if places else Decimal("1")
        if quantity != quantity.quantize(quant):
            raise AppError(
                f"esta unidad admite como máximo {places} decimales",
                400,
            )
    return quantity


def _next_quantity(current: Decimal, kind: str, amount: Decimal) -> Decimal:
    """Calcula el stock resultante según el tipo de movimiento."""
    if kind == MovementKind.ENTRADA:
        return current + amount
    if kind == MovementKind.SALIDA:
        return current - amount
    return amount
