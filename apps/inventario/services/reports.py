"""Reportes del inventario.

Propósito: ajustes, consumo, préstamos y pedido sugerido con sus filtros.
Contexto: solo lectura; reutiliza los permisos del inventario y el stock
que ya está en los movimientos, sin guardar cifras aparte.
@author Cristian Deysdayr Jimenez
"""
from datetime import date, timedelta
from decimal import Decimal
from django.db.models import F, Sum
from apps.accounts.errors import AppError
from apps.accounts.models import User
from apps.inventario.catalog import plain_amount
from apps.inventario.models import (
    AdjustmentReason,
    Movement,
    MovementKind,
    Product,
    ProductLoan,
)
from apps.inventario.services import products as product_services
from apps.inventario.services.membership import require_membership
from apps.inventario.services.permissions import READ, require_action
ZERO = Decimal("0")
MONTHS = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)
def _guard(
    *,
    user: User,
    farm_id: int,
    date_from: date | None,
    date_to: date | None,
):
    """Exige lectura de la finca y un rango de fechas coherente."""
    membership = require_membership(user=user, farm_id=farm_id)
    require_action(membership, READ)
    if date_from is not None and date_to is not None and date_from > date_to:
        raise AppError("el rango de fechas no es válido", 400)
    return membership
def _in_range(queryset, date_from: date | None, date_to: date | None):
    """Recorta el queryset al rango de fechas, si lo hay."""
    if date_from is not None:
        queryset = queryset.filter(created_at__date__gte=date_from)
    if date_to is not None:
        queryset = queryset.filter(created_at__date__lte=date_to)
    return queryset
def _user_name(user: User) -> str:
    """Nombre visible de quien registró el movimiento."""
    return f"{user.first_name} {user.last_name}".strip() or user.identification
def _reason_label(reason: str) -> str:
    """Texto del motivo, o aviso cuando el ajuste es viejo."""
    if not reason:
        return "Sin motivo"
    return AdjustmentReason(reason).label
def adjustment_report(
    *,
    user: User,
    farm_id: int,
    date_from: date | None = None,
    date_to: date | None = None,
    product_id: int | None = None,
    reason: str | None = None,
) -> dict:
    """Reconteos hechos en el rango, con su motivo y su diferencia."""
    _guard(user=user, farm_id=farm_id, date_from=date_from, date_to=date_to)
    if reason and reason not in AdjustmentReason.values:
        raise AppError("motivo de ajuste inválido", 400)
    queryset = Movement.objects.filter(
        product__farm_id=farm_id,
        kind=MovementKind.AJUSTE,
    ).select_related("product", "created_by", "unit")
    queryset = _in_range(queryset, date_from, date_to)
    if product_id is not None:
        queryset = queryset.filter(product_id=product_id)
    if reason:
        queryset = queryset.filter(reason=reason)

    rows = []
    up = 0
    down = 0
    for move in queryset:
        difference = move.stock_after - move.stock_before
        if difference > ZERO:
            up += 1
        elif difference < ZERO:
            down += 1
        rows.append(
            {
                "id": move.id,
                "when": move.created_at,
                "product_id": move.product_id,
                "product": move.product.name,
                "reason": move.reason,
                "reason_label": _reason_label(move.reason),
                "note": move.note,
                "before": plain_amount(move.stock_before),
                "after": plain_amount(move.stock_after),
                "difference": plain_amount(difference),
                "unit": move.unit.label,
                "user": _user_name(move.created_by),
            }
        )
    return {"rows": rows, "count": len(rows), "up": up, "down": down}
def products_for_filter(*, user: User, farm_id: int) -> list[Product]:
    """Productos de la finca para los selectores de los reportes."""
    membership = require_membership(user=user, farm_id=farm_id)
    require_action(membership, READ)
    return list(
        Product.objects.filter(farm_id=farm_id, is_removed=False)
        .select_related("unit")
        .order_by("name")
    )
