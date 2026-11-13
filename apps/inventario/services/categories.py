"""Alta de categorías del catálogo.

Propósito: crear una categoría y reutilizar unidades que ya existen.
@author Cristian Deysdayr Jimenez
"""
import re
import unicodedata

from django.db import transaction

from apps.accounts.errors import AppError
from apps.accounts.models import User
from apps.inventario.models import CategoryUnit, InventoryCategory, InventoryUnit, Product
from apps.inventario.services.membership import require_membership
from apps.inventario.services.permissions import CREATE_UNIT, require_action
from apps.inventario.services.units import create_custom_unit


def create_user_category(
    *,
    user: User,
    farm_id: int,
    name: str,
    copy_from: str = "",
    unit_codes: list[str] | None = None,
    unit_label: str = "",
    base_unit_code: str = "",
    factor=None,
) -> InventoryCategory:
    """Crea la categoría y, si hace falta, una unidad o el vínculo con otras."""
    membership = require_membership(user=user, farm_id=farm_id)
    require_action(membership, CREATE_UNIT)
    clean_name = name.strip()
    if not clean_name:
        raise AppError("escribe el nombre de la categoría", 400)
    if len(clean_name) > 80:
        raise AppError("el nombre de la categoría es demasiado largo", 400)

    with transaction.atomic():
        category = InventoryCategory.objects.create(code=_fresh_code(clean_name), name=clean_name)
        _copy_units(category, copy_from, unit_codes or [])
        if unit_label.strip():
            create_custom_unit(
                user=user,
                farm_id=farm_id,
                stem=_slug(unit_label),
                label=unit_label.strip(),
                base_unit_code=base_unit_code or "u",
                factor=factor if factor is not None else 1,
                category_codes=[category.code],
            )
    return category


def rename_category(
    *,
    user: User,
    farm_id: int,
    code: str,
    name: str,
    unit_codes: list[str] | None = None,
) -> InventoryCategory:
    """Cambia el nombre y, si se indican, las unidades de la categoría."""
    membership = require_membership(user=user, farm_id=farm_id)
    require_action(membership, CREATE_UNIT)
    category = _active_category(code)
    clean_name = name.strip()
    if not clean_name:
        raise AppError("escribe el nombre de la categoría", 400)
    if len(clean_name) > 80:
        raise AppError("el nombre de la categoría es demasiado largo", 400)
    taken = InventoryCategory.objects.filter(name__iexact=clean_name).exclude(pk=category.pk)
    if taken.exists():
        raise AppError("ya hay una categoría con ese nombre", 400)
    units = _units_for(unit_codes) if unit_codes is not None else None
    with transaction.atomic():
        category.name = clean_name
        category.save(update_fields=["name"])
        if units is not None:
            CategoryUnit.objects.filter(category=category).exclude(unit__in=units).delete()
            for unit in units:
                CategoryUnit.objects.get_or_create(category=category, unit=unit)
    return category


def delete_category(*, user: User, farm_id: int, code: str) -> None:
    """Borra la categoría si ningún producto la usa."""
    membership = require_membership(user=user, farm_id=farm_id)
    require_action(membership, CREATE_UNIT)
    category = _active_category(code)
    if Product.objects.filter(category=category).exists():
        raise AppError("esa categoría está en un producto", 400)
    category.delete()


def _units_for(unit_codes: list[str]) -> list[InventoryUnit]:
    """Valida que las unidades existan y haya al menos una."""
    if not unit_codes:
        raise AppError("indica al menos una unidad", 400)
    units = list(InventoryUnit.objects.filter(code__in=unit_codes, is_active=True))
    if len(units) != len(set(unit_codes)):
        raise AppError("una o más unidades no son válidas", 400)
    return units


def _active_category(code: str) -> InventoryCategory:
    """Busca una categoría activa por su código."""
    category = InventoryCategory.objects.filter(code=code, is_active=True).first()
    if category is None:
        raise AppError("no se encontró la categoría", 404)
    return category


def _copy_units(category: InventoryCategory, copy_from: str, unit_codes: list[str]) -> None:
    """Enlaza unidades ya existentes, sin duplicarlas."""
    if not copy_from and not unit_codes:
        return
    chosen = InventoryUnit.objects.filter(is_active=True)
    if unit_codes:
        chosen = chosen.filter(code__in=unit_codes)
    elif copy_from:
        linked = CategoryUnit.objects.filter(category__code=copy_from).values_list(
            "unit_id",
            flat=True,
        )
        chosen = chosen.filter(id__in=linked)
    else:
        return
    if copy_from and unit_codes:
        linked = CategoryUnit.objects.filter(category__code=copy_from).values_list(
            "unit_id",
            flat=True,
        )
        chosen = chosen.filter(id__in=linked)
    for unit in chosen:
        CategoryUnit.objects.get_or_create(category=category, unit=unit)


def _fresh_code(name: str) -> str:
    """Arma un código libre a partir del nombre."""
    stem = _slug(name)[:32]
    code = stem
    number = 2
    while InventoryCategory.objects.filter(code=code).exists():
        code = f"{stem[:28]}_{number}"
        number += 1
    return code


def _slug(value: str) -> str:
    """Normaliza un nombre a código."""
    normalized = unicodedata.normalize("NFKD", value.strip().lower())
    ascii_text = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    slug = re.sub(r"[^a-z0-9]+", "_", ascii_text).strip("_")
    if not slug:
        raise AppError("el nombre no es válido", 400)
    return slug[:40]
