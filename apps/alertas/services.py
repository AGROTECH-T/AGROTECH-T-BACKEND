"""Avisos del sistema.

Propósito: mostrar stock bajo/agotado y bitácora diaria pendiente.
Contexto: se calculan al vuelo; no se guardan avisos aparte.
@author Cristian Deysdayr Jimenez
"""
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db.models import F
from django.utils import timezone

from apps.accounts.models import User
from apps.farms.models import FarmMembership, MembershipStatus
from apps.inventario.catalog import plain_amount
from apps.inventario.models import Product
from apps.production.avicultura.models import EventKind, Lot, LotEvent, LotStatus

ZERO = Decimal("0")


def list_alerts(*, user: User) -> list[dict]:
    """Avisos de stock y recordatorios de bitácora del usuario."""
    farm_ids = _farm_ids(user)
    if not farm_ids:
        return []
    return [*_stock_alerts(farm_ids), *_bitacora_alerts(farm_ids)]


def _farm_ids(user: User) -> list[int]:
    """Fincas activas donde la cuenta sigue con membresía activa."""
    return list(
        FarmMembership.objects.filter(
            user=user,
            status=MembershipStatus.ACTIVE,
            farm__is_active=True,
        ).values_list("farm_id", flat=True)
    )


def _stock_alerts(farm_ids: list[int]) -> list[dict]:
    """Productos que ya llegaron a su mínimo o se agotaron."""
    products = (
        Product.objects.filter(
            farm_id__in=farm_ids,
            is_removed=False,
            is_active=True,
            quantity__lte=F("min_stock"),
        )
        .select_related("farm", "unit")
        .order_by("farm__name", "name")
    )
    return [_stock_alert(item) for item in products]


def _stock_alert(product: Product) -> dict:
    """Arma el aviso de un producto."""
    empty = product.quantity <= ZERO
    return {
        "id": f"stock:{product.id}",
        "kind": "agotado" if empty else "bajo",
        "title": f"{product.name} se agotó" if empty else f"{product.name} está bajo",
        "detail": (
            f"Quedan {plain_amount(product.quantity)} {product.unit.label} "
            f"y el mínimo es {plain_amount(product.min_stock)}"
        ),
        "farm_id": product.farm_id,
        "farm": product.farm.name,
        "product_id": product.id,
        "product": product.name,
        "quantity": plain_amount(product.quantity),
        "min": plain_amount(product.min_stock),
        "unit": product.unit.label,
    }


def _bitacora_alerts(farm_ids: list[int]) -> list[dict]:
    """Lotes abiertos sin ronda registrada hoy (zona local del servidor)."""
    lots = list(
        Lot.objects.filter(farm_id__in=farm_ids, status=LotStatus.ABIERTO)
        .select_related("farm", "house")
        .order_by("farm__name", "code")
    )
    if not lots:
        return []

    today = timezone.localdate()
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(today, time.min), tz)
    end = start + timedelta(days=1)
    done = set(
        LotEvent.objects.filter(
            lot_id__in=[lot.id for lot in lots],
            kind=EventKind.RONDA,
            occurred_at__gte=start,
            occurred_at__lt=end,
        ).values_list("lot_id", flat=True)
    )
    return [_bitacora_alert(lot) for lot in lots if lot.id not in done]


def _bitacora_alert(lot: Lot) -> dict:
    """Recordatorio de llenar la bitácora diaria de un lote."""
    return {
        "id": f"bitacora:{lot.id}",
        "kind": "bitacora",
        "title": f"Bitácora pendiente · {lot.code}",
        "detail": "No hay ronda registrada hoy.",
        "farm_id": lot.farm_id,
        "farm": lot.farm.name,
        "lot_id": lot.id,
        "lot": lot.code,
        "house_id": lot.house_id,
        "house": lot.house.name,
    }
