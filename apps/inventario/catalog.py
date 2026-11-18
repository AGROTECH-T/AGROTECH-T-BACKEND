"""Lectura del catálogo de inventario.

Propósito: armar el payload de selects (categorías y unidades con equivalencia).
Contexto: datos viven en tablas sembradas; ya no son TextChoices fijos.
@author Cristian Deysdayr Jimenez
"""
from apps.inventario.models import CategoryUnit, InventoryBaseUnit, InventoryCategory, InventoryUnit


def catalog_payload(*, category_code: str | None = None) -> dict:
    """Lista categorías y unidades activas para el cliente."""
    categories = list(
        InventoryCategory.objects.filter(is_active=True).order_by("name")
    )
    units_qs = InventoryUnit.objects.filter(is_active=True).select_related("base_unit")
    if category_code:
        unit_ids = CategoryUnit.objects.filter(
            category__code=category_code,
            category__is_active=True,
        ).values_list("unit_id", flat=True)
        units_qs = units_qs.filter(id__in=unit_ids)

    units = list(units_qs.order_by("label"))
    links = CategoryUnit.objects.filter(
        category__is_active=True,
        unit__is_active=True,
    ).select_related("category", "unit")
    by_category: dict[str, list[str]] = {}
    for link in links:
        by_category.setdefault(link.category.code, []).append(link.unit.code)

    return {
        "categories": [
            {
                "id": item.code,
                "label": item.name,
                "description": item.description,
            }
            for item in categories
        ],
        "units": [_unit_payload(item) for item in units],
        "units_by_category": by_category,
        "base_units": [
            {
                "id": item.code,
                "label": f"{item.name} ({item.symbol})",
                "name": item.name,
                "symbol": item.symbol,
                "nature": item.nature,
            }
            for item in InventoryBaseUnit.objects.order_by("name")
        ],
    }


def plain_amount(value) -> str:
    """Número sin ceros de más: 120.000000 queda 120."""
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _unit_payload(unit: InventoryUnit) -> dict:
    """Serializa una unidad con equivalencia visible."""
    base = unit.base_unit
    factor = unit.factor
    equivalent = None
    if factor != 1 or unit.code != base.code:
        equivalent = f"{plain_amount(factor)} {base.symbol}"
    return {
        "id": unit.code,
        "label": unit.label,
        "nature": unit.nature,
        "decimal_places": unit.decimal_places,
        "factor": str(factor),
        "base_unit": base.code,
        "base_symbol": base.symbol,
        "equivalent": equivalent,
        "is_system": unit.is_system,
    }
