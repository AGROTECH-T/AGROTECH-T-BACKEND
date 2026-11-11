"""Creación de unidades/presentaciones por el usuario.

Propósito: completar el catálogo cuando falta una presentación comercial.
Contexto: el código interno se genera (p. ej. bulto_50kg); no lo escribe el usuario.
@author Cristian Deysdayr Jimenez
"""
import re
import unicodedata
from decimal import Decimal

from django.db import IntegrityError, transaction

from apps.accounts.errors import AppError
from apps.accounts.models import User
from apps.inventario.models import (
    CategoryUnit,
    InventoryBaseUnit,
    InventoryCategory,
    InventoryUnit,
    MagnitudeNature,
    Product,
)
from apps.inventario.services.membership import require_membership
from apps.inventario.services.permissions import CREATE_UNIT, require_action

ZERO = Decimal("0")


def create_custom_unit(
    *,
    user: User,
    farm_id: int,
    stem: str,
    label: str,
    base_unit_code: str,
    factor: Decimal,
    category_codes: list[str],
    nature: str | None = None,
) -> InventoryUnit:
    """Crea una presentación de usuario y la asocia a categorías."""
    membership = require_membership(user=user, farm_id=farm_id)
    require_action(membership, CREATE_UNIT)

    clean_stem = _slug_stem(stem)
    clean_label = _required_text(label, "el nombre visible de la unidad es obligatorio", 80)
    if factor <= ZERO:
        raise AppError("el factor de conversión debe ser mayor que cero", 400)
    if not category_codes:
        raise AppError("indica al menos una categoría para la unidad", 400)

    base = InventoryBaseUnit.objects.filter(code=base_unit_code).first()
    if base is None:
        raise AppError("unidad base no válida", 400)

    categories = list(
        InventoryCategory.objects.filter(code__in=category_codes, is_active=True)
    )
    if len(categories) != len(set(category_codes)):
        raise AppError("una o más categorías no son válidas", 400)

    unit_nature = nature or (
        MagnitudeNature.PRESENTACION
        if factor != 1
        else base.nature
    )
    if unit_nature not in MagnitudeNature.values:
        raise AppError("naturaleza de magnitud no válida", 400)

    code = build_unit_code(clean_stem, factor, base.symbol)
    decimals = 0 if unit_nature != MagnitudeNature.CONTINUA else base.decimal_places

    existing = InventoryUnit.objects.filter(code=code).first()
    if existing is not None:
        _link_categories(existing, categories)
        return existing

    try:
        with transaction.atomic():
            unit = InventoryUnit.objects.create(
                code=code,
                label=clean_label,
                base_unit=base,
                factor=factor,
                nature=unit_nature,
                decimal_places=decimals,
                is_system=False,
                is_active=True,
                created_by=user,
            )
            _link_categories(unit, categories)
            return unit
    except IntegrityError as exc:
        raise AppError("esa unidad ya existe", 409) from exc


def create_base_unit(
    *,
    user: User,
    farm_id: int,
    name: str,
    symbol: str,
    decimals: bool,
) -> InventoryBaseUnit:
    """Crea una medida base que el catálogo todavía no trae."""
    membership = require_membership(user=user, farm_id=farm_id)
    require_action(membership, CREATE_UNIT)
    clean_name = _required_text(name, "escribe el nombre de la medida", 60)
    clean_symbol = _symbol(symbol)
    code = _slug_stem(clean_name)[:20]
    if InventoryBaseUnit.objects.filter(code=code).exists():
        raise AppError("esa medida ya existe", 409)
    if InventoryBaseUnit.objects.filter(symbol__iexact=clean_symbol).exists():
        raise AppError("ese símbolo ya está en una medida", 409)
    nature = MagnitudeNature.CONTINUA if decimals else MagnitudeNature.DISCRETA
    places = 3 if decimals else 0
    try:
        return InventoryBaseUnit.objects.create(
            code=code,
            name=clean_name,
            symbol=clean_symbol,
            nature=nature,
            decimal_places=places,
        )
    except IntegrityError as exc:
        raise AppError("esa medida ya existe", 409) from exc


def update_base_unit(
    *,
    user: User,
    farm_id: int,
    code: str,
    name: str,
    symbol: str,
    decimals: bool,
) -> InventoryBaseUnit:
    """Cambia el nombre, el símbolo y si la medida admite decimales."""
    membership = require_membership(user=user, farm_id=farm_id)
    require_action(membership, CREATE_UNIT)
    base = _base(code)
    clean_name = _required_text(name, "escribe el nombre de la medida", 60)
    clean_symbol = _symbol(symbol)
    if InventoryBaseUnit.objects.filter(symbol__iexact=clean_symbol).exclude(pk=base.pk).exists():
        raise AppError("ese símbolo ya está en una medida", 409)
    base.name = clean_name
    base.symbol = clean_symbol
    base.nature = MagnitudeNature.CONTINUA if decimals else MagnitudeNature.DISCRETA
    base.decimal_places = 3 if decimals else 0
    base.save(update_fields=["name", "symbol", "nature", "decimal_places"])
    InventoryUnit.objects.filter(base_unit=base, factor=1).exclude(
        nature=MagnitudeNature.PRESENTACION
    ).update(nature=base.nature, decimal_places=base.decimal_places)
    return base


def delete_base_unit(*, user: User, farm_id: int, code: str) -> None:
    """Borra la medida si ningún conteo la usa."""
    membership = require_membership(user=user, farm_id=farm_id)
    require_action(membership, CREATE_UNIT)
    base = _base(code)
    if InventoryUnit.objects.filter(base_unit=base).exists():
        raise AppError("esa medida se usa para contar", 400)
    base.delete()


def _base(code: str) -> InventoryBaseUnit:
    """Busca una medida por su código."""
    base = InventoryBaseUnit.objects.filter(code=code).first()
    if base is None:
        raise AppError("no se encontró la medida", 404)
    return base


def update_unit(
    *,
    user: User,
    farm_id: int,
    code: str,
    label: str,
    category_codes: list[str],
) -> InventoryUnit:
    """Cambia el nombre visible y las categorías de una unidad."""
    membership = require_membership(user=user, farm_id=farm_id)
    require_action(membership, CREATE_UNIT)
    unit = _active_unit(code)
    clean_label = _required_text(label, "el nombre visible de la unidad es obligatorio", 80)
    if not category_codes:
        raise AppError("indica al menos una categoría para la unidad", 400)
    categories = list(InventoryCategory.objects.filter(code__in=category_codes, is_active=True))
    if len(categories) != len(set(category_codes)):
        raise AppError("una o más categorías no son válidas", 400)
    with transaction.atomic():
        unit.label = clean_label
        unit.save(update_fields=["label"])
        CategoryUnit.objects.filter(unit=unit).exclude(category__in=categories).delete()
        _link_categories(unit, categories)
    return unit


def delete_unit(*, user: User, farm_id: int, code: str) -> None:
    """Borra la unidad si ningún producto la usa."""
    membership = require_membership(user=user, farm_id=farm_id)
    require_action(membership, CREATE_UNIT)
    unit = _active_unit(code)
    if Product.objects.filter(unit=unit).exists():
        raise AppError("esa unidad está en un producto", 400)
    unit.delete()


def _active_unit(code: str) -> InventoryUnit:
    """Busca una unidad activa por su código."""
    unit = InventoryUnit.objects.filter(code=code, is_active=True).first()
    if unit is None:
        raise AppError("no se encontró la unidad", 404)
    return unit


def build_unit_code(stem: str, factor: Decimal, base_symbol: str) -> str:
    """Arma el código interno: bulto_50kg, caneca_20l."""
    factor_part = _format_factor(factor)
    prepared = base_symbol.replace("³", "3").replace("²", "2").replace("@", "at")
    symbol = _slug_stem(prepared)
    return f"{stem}_{factor_part}{symbol}"


def _link_categories(unit: InventoryUnit, categories: list[InventoryCategory]) -> None:
    """Asocia la unidad a las categorías indicadas."""
    for category in categories:
        CategoryUnit.objects.get_or_create(category=category, unit=unit)


def _slug_stem(value: str) -> str:
    """Normaliza un texto a identificador sin tildes ni espacios."""
    normalized = unicodedata.normalize("NFKD", value.strip().lower())
    ascii_text = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    slug = re.sub(r"[^a-z0-9]+", "_", ascii_text).strip("_")
    if not slug:
        raise AppError("el nombre corto de la unidad no es válido", 400)
    if len(slug) > 30:
        raise AppError("el nombre corto de la unidad es demasiado largo", 400)
    return slug


def _format_factor(factor: Decimal) -> str:
    """Quita ceros sobrantes del factor para el código."""
    normalized = factor.normalize()
    text = format(normalized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text.replace(".", "p")


def _symbol(value: str) -> str:
    """Exige un símbolo corto, sin espacios."""
    clean = value.strip()
    if not clean or len(clean) > 10 or " " in clean:
        raise AppError("escribe un símbolo corto, sin espacios", 400)
    return clean


def _required_text(value: str, empty_message: str, max_length: int) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise AppError(empty_message, 400)
    if len(cleaned) > max_length:
        raise AppError(f"no puede superar los {max_length} caracteres", 400)
    return cleaned
