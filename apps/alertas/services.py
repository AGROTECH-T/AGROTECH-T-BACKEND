"""Avisos del sistema.

Propósito: mostrar los productos en o bajo el mínimo de las fincas del usuario.
Contexto: se calculan al vuelo desde el stock; no se guardan avisos aparte.
@author Cristian Deysdayr Jimenez
"""
from django.db.models import F

from apps.accounts.models import User
from apps.farms.models import FarmMembership
from apps.inventario.catalog import plain_amount
from apps.inventario.models import Product

ZERO = 0


def list_alerts(*, user: User) -> list[dict]:
    """Productos que ya llegaron a su mínimo o se agotaron."""
    farm_ids = list(
        FarmMembership.objects.filter(user=user).values_list("farm_id", flat=True)
    )
    if not farm_ids:
        return []
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
    return [_alert(item) for item in products]


def _alert(product: Product) -> dict:
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
