"""Importación de inventario desde la plantilla de Excel.

Propósito: crear o actualizar los productos de una finca con el archivo que
descarga la persona, completando el catálogo cuando aparece algo nuevo.
Contexto: el stock entra por movimiento (entrada o ajuste), nunca escribiendo
la cantidad a mano; una fila con error no detiene a las demás.
@author Cristian Deysdayr Jimenez
"""
import re
import unicodedata
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction

from apps.accounts.errors import AppError
from apps.accounts.models import User
from apps.inventario.models import (
    AdjustmentReason,
    CategoryUnit,
    InventoryBaseUnit,
    InventoryCategory,
    InventoryUnit,
    MovementKind,
    Product,
)
from apps.inventario.services import products as product_services
from apps.inventario.services.categories import create_user_category
from apps.inventario.services.membership import require_membership
from apps.inventario.services.movements import register_movement
from apps.inventario.services.permissions import WRITE_STOCK, require_action
from apps.inventario.services.units import create_custom_unit
from apps.inventario.services.xlsx_reader import read_rows

COLUMNS = (
    "Producto",
    "Código",
    "Categoría",
    "Unidad",
    "Stock actual",
    "Equivalencia",
    "Stock mínimo",
)

_HEADERS = {
    "producto": "name",
    "codigo": "barcode",
    "codigo de barras": "barcode",
    "categoria": "category",
    "unidad": "unit",
    "stock actual": "quantity",
    "cantidad": "quantity",
    "equivalencia": "equivalent",
    "stock minimo": "min_stock",
    "minimo": "min_stock",
}

_REQUIRED = (
    ("name", "Producto"),
    ("barcode", "Código"),
    ("category", "Categoría"),
    ("unit", "Unidad"),
    ("quantity", "Stock actual"),
    ("equivalent", "Equivalencia"),
    ("min_stock", "Stock mínimo"),
)

_MEASURE = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*(\S.*)$")
_PARENTHESIS = re.compile(r"\(([^)]*)\)\s*$")
_ZERO = Decimal("0")
_LIMIT = Decimal("1000000000")


def import_inventory(
    *,
    user: User,
    farm_id: int,
    payload: bytes,
    filename: str = "",
) -> dict:
    """Aplica la plantilla y devuelve el resumen de la corrida."""
    membership = require_membership(user=user, farm_id=farm_id)
    require_action(membership, WRITE_STOCK)
    rows = read_template(payload)
    if not rows:
        raise AppError("la plantilla no trae productos", 400)

    categories_before = InventoryCategory.objects.count()
    units_before = InventoryUnit.objects.count()
    created = 0
    updated = 0
    unchanged = 0
    errors = []
    created_ids = []
    for position, values in enumerate(rows, start=2):
        try:
            outcome = _apply_row(
                user=user,
                membership=membership,
                values=values,
            )
        except AppError as exc:
            errors.append({"row": position, "message": str(exc)})
            continue
        except IntegrityError:
            errors.append(
                {
                    "row": position,
                    "message": "no se pudo guardar: revisa el código o el nombre",
                }
            )
            continue
        if outcome["created"]:
            created += 1
            created_ids.append(outcome["product"].id)
        elif outcome["changed"]:
            updated += 1
        else:
            unchanged += 1

    return {
        "file": filename,
        "total": created + updated + unchanged + len(errors),
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "categories": InventoryCategory.objects.count() - categories_before,
        "units": InventoryUnit.objects.count() - units_before,
        "created_ids": created_ids,
        "errors": errors,
    }


def read_template(payload: bytes) -> list[dict[str, str]]:
    """Filas de la plantilla ya etiquetadas con el nombre de cada columna."""
    rows = read_rows(payload)
    if not rows:
        raise AppError("el archivo no tiene filas", 400)
    positions = _positions(rows[0])
    return [
        {key: _cell(row, positions[key]) for key, _ in _REQUIRED}
        for row in rows[1:]
        if any(cell.strip() for cell in row)
    ]


def _positions(headers: list[str]) -> dict[str, int]:
    """Columna de cada dato, según los nombres del encabezado."""
    found = {}
    for index, cell in enumerate(headers):
        key = _HEADERS.get(_normalize(cell))
        if key and key not in found:
            found[key] = index
    missing = [label for key, label in _REQUIRED if key not in found]
    if missing:
        raise AppError("faltan columnas: " + ", ".join(missing), 400)
    return found


def _cell(row: list[str], index: int) -> str:
    """Celda tolerante a filas más cortas que el encabezado."""
    if index >= len(row):
        return ""
    return row[index]


def _apply_row(*, user: User, membership, values: dict[str, str]) -> dict:
    """Crea o actualiza un producto con su stock."""
    clean_name = _text(values["name"], "el producto no tiene nombre", 100)
    clean_barcode = _barcode(values["barcode"])
    clean_category = _text(values["category"], "el producto no tiene categoría", 80)
    clean_unit = _text(values["unit"], "el producto no tiene unidad", 80)
    counted = _amount(values["quantity"], "el stock actual")
    minimum = _amount(values["min_stock"], "el stock mínimo")

    with transaction.atomic():
        category = _category(
            user=user,
            farm_id=membership.farm_id,
            name=clean_category,
        )
        unit = _unit(
            user=user,
            farm_id=membership.farm_id,
            category=category,
            name=clean_unit,
            equivalent=values["equivalent"],
        )
        product = _find_product(
            farm_id=membership.farm_id,
            name=clean_name,
            barcode=clean_barcode,
        )
        if product is None:
            product = product_services.create_product(
                user=user,
                farm_id=membership.farm_id,
                name=clean_name,
                barcode=clean_barcode,
                category_code=category.code,
                unit_code=unit.code,
                min_stock=minimum,
            )
            _set_stock(
                user=user,
                farm_id=membership.farm_id,
                product=product,
                quantity=counted,
                is_new=True,
            )
            return {"created": True, "changed": True, "product": product}

        changed = _refresh_product(
            user=user,
            farm_id=membership.farm_id,
            product=product,
            name=clean_name,
            barcode=clean_barcode,
            category_code=category.code,
            unit_code=unit.code,
            min_stock=minimum,
        )
        moved = _set_stock(
            user=user,
            farm_id=membership.farm_id,
            product=product,
            quantity=counted,
            is_new=False,
        )
        return {"created": False, "changed": changed or moved, "product": product}


def _category(*, user: User, farm_id: int, name: str) -> InventoryCategory:
    """Busca la categoría por nombre o la crea."""
    category = InventoryCategory.objects.filter(
        name__iexact=name,
        is_active=True,
    ).first()
    if category is not None:
        return category
    return create_user_category(user=user, farm_id=farm_id, name=name)


def _unit(
    *,
    user: User,
    farm_id: int,
    category: InventoryCategory,
    name: str,
    equivalent: str,
) -> InventoryUnit:
    """Busca la unidad o la crea a partir de su equivalencia."""
    unit = InventoryUnit.objects.filter(label__iexact=name, is_active=True).first()
    if unit is None:
        unit = _base_presentation(name)
    if unit is None:
        factor, base = _measure(name, equivalent)
        unit = create_custom_unit(
            user=user,
            farm_id=farm_id,
            stem=_stem(name)[:30],
            label=name,
            base_unit_code=base.code,
            factor=factor,
            category_codes=[category.code],
        )
    CategoryUnit.objects.get_or_create(category=category, unit=unit)
    return unit


def _base_presentation(name: str) -> InventoryUnit | None:
    """Unidad que solo nombra una medida base, como Kilogramo."""
    base = _base(name)
    if base is None:
        return None
    return InventoryUnit.objects.filter(base_unit=base, factor=1, is_active=True).first()


def _measure(name: str, equivalent: str) -> tuple[Decimal, InventoryBaseUnit]:
    """Factor y medida base que describe la unidad nueva."""
    content = _inside(name) or equivalent.strip()
    match = _MEASURE.match(content)
    if match is not None:
        try:
            factor = Decimal(match.group(1).replace(",", "."))
        except InvalidOperation as exc:
            raise AppError("la equivalencia no es un número válido") from exc
        if factor <= _ZERO:
            raise AppError("la equivalencia debe ser mayor que cero")
        base = _base(match.group(2))
        if base is None:
            raise AppError(f"no existe la medida «{match.group(2)}»")
        return factor, base
    base = InventoryBaseUnit.objects.filter(code="u").first()
    if base is None:
        raise AppError("el catálogo no tiene medidas para crear la unidad")
    return Decimal("1"), base


def _base(value: str) -> InventoryBaseUnit | None:
    """Medida base por símbolo, código o nombre."""
    clean = value.strip()
    if not clean:
        return None
    for field in ("symbol", "code", "name"):
        found = InventoryBaseUnit.objects.filter(**{f"{field}__iexact": clean}).first()
        if found is not None:
            return found
    return None


def _find_product(*, farm_id: int, name: str, barcode: str) -> Product | None:
    """Producto que corresponde a la fila: primero por código, luego por nombre."""
    queryset = Product.objects.filter(farm_id=farm_id, is_removed=False)
    if barcode:
        found = queryset.filter(barcode=barcode).first()
        if found is not None:
            return found
    matches = list(queryset.filter(name__iexact=name)[:2])
    if len(matches) > 1:
        raise AppError(f"hay más de un producto llamado «{name}»")
    return matches[0] if matches else None


def _refresh_product(
    *,
    user: User,
    farm_id: int,
    product: Product,
    name: str,
    barcode: str,
    category_code: str,
    unit_code: str,
    min_stock: Decimal,
) -> bool:
    """Actualiza los datos del producto y dice si algo cambió."""
    if (
        product.name == name
        and product.barcode == barcode
        and product.category.code == category_code
        and product.unit.code == unit_code
        and product.min_stock == min_stock
    ):
        return False
    product_services.update_product(
        user=user,
        farm_id=farm_id,
        product_id=product.id,
        name=name,
        barcode=barcode,
        category_code=category_code,
        unit_code=unit_code,
        min_stock=min_stock,
    )
    return True


def _set_stock(
    *,
    user: User,
    farm_id: int,
    product: Product,
    quantity: Decimal,
    is_new: bool,
) -> bool:
    """Lleva el stock al conteo por medio de un movimiento."""
    if quantity == product.quantity:
        return False
    if is_new and product.quantity == _ZERO:
        register_movement(
            user=user,
            farm_id=farm_id,
            product_id=product.id,
            kind=MovementKind.ENTRADA,
            quantity=quantity,
            note="Inventario importado",
        )
        return True
    register_movement(
        user=user,
        farm_id=farm_id,
        product_id=product.id,
        kind=MovementKind.AJUSTE,
        quantity=quantity,
        note="Importación de inventario",
        reason=AdjustmentReason.CONTEO,
    )
    return True


def _amount(value: str, label: str) -> Decimal:
    """Cantidad no negativa con hasta tres decimales."""
    text = value.strip().replace(",", ".")
    if not text:
        return _ZERO
    try:
        amount = Decimal(text)
    except InvalidOperation as exc:
        raise AppError(f"{label} no es un número válido") from exc
    if amount < _ZERO:
        raise AppError(f"{label} no puede ser negativo")
    if amount >= _LIMIT:
        raise AppError(f"{label} es demasiado grande")
    if amount != amount.quantize(Decimal("0.001")):
        raise AppError(f"{label} admite hasta tres decimales")
    return amount


def _barcode(value: str) -> str:
    """Código opcional, único en la finca y de 64 caracteres como máximo."""
    clean = value.strip()
    if len(clean) > 64:
        raise AppError("el código no puede superar los 64 caracteres")
    return clean


def _text(value: str, empty_message: str, max_length: int) -> str:
    """Texto obligatorio con un largo máximo."""
    clean = value.strip()
    if not clean:
        raise AppError(empty_message)
    if len(clean) > max_length:
        raise AppError(f"no puede superar los {max_length} caracteres")
    return clean


def _inside(value: str) -> str:
    """Contenido entre paréntesis al final del nombre: Bulto (50 kg)."""
    match = _PARENTHESIS.search(value)
    return match.group(1).strip() if match else ""


def _stem(value: str) -> str:
    """Nombre de la unidad sin la equivalencia entre paréntesis."""
    return _PARENTHESIS.sub("", value).strip()


def _normalize(value: str) -> str:
    """Encabezado sin tildes, en minúsculas y con un solo espacio."""
    normalized = unicodedata.normalize("NFKD", value.strip().lower())
    ascii_text = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return " ".join(ascii_text.split())
