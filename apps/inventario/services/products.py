"""Casos de uso de insumos de inventario.

Propósito: listar, crear, editar y activar/desactivar productos por finca.
Contexto: categoría y unidad salen del catálogo; la cantidad solo cambia por movimientos.
@author Cristian Deysdayr Jimenez
"""
from decimal import Decimal

from django.db import IntegrityError
from django.db.models import (
    DecimalField,
    F,
    Max,
    OuterRef,
    Q,
    QuerySet,
    Subquery,
    Sum,
    Value,
)
from django.db.models.functions import Coalesce

from apps.accounts.errors import AppError
from apps.accounts.models import User
from apps.inventario.models import (
    CategoryUnit,
    InventoryCategory,
    InventoryUnit,
    Product,
    ProductLoan,
)
from apps.inventario.services.membership import require_membership
from apps.inventario.services.permissions import (
    MANAGE_PRODUCT,
    READ,
    WRITE_STOCK,
    require_action,
)

ZERO = Decimal("0")


def list_products(
    *,
    user: User,
    farm_id: int,
    include_inactive: bool = False,
    category_code: str | None = None,
    low_stock_only: bool = False,
    search: str | None = None,
    status: str | None = None,
    order: str = "name",
) -> QuerySet[Product]:
    """Lista insumos de una finca del usuario."""
    membership = require_membership(user=user, farm_id=farm_id)
    require_action(membership, READ)
    if order not in ("name", "quantity", "last_movement"):
        raise AppError("orden no válido", 400)
    if status not in (None, "", "active", "inactive", "all"):
        raise AppError("estado no válido", 400)

    queryset = _with_lent(
        Product.objects.filter(farm_id=farm_id, is_removed=False).select_related(
            "category",
            "unit",
            "unit__base_unit",
        ).annotate(last_movement_at=Max("movements__created_at"))
    )
    queryset = _apply_status(queryset, status=status, include_inactive=include_inactive)
    if category_code:
        queryset = queryset.filter(category__code=category_code)
    if low_stock_only:
        queryset = queryset.filter(quantity__lte=F("min_stock"))
    if search:
        term = search.strip()
        if term:
            queryset = queryset.filter(
                Q(name__icontains=term) | Q(barcode__icontains=term)
            )
    return _apply_order(queryset, order)


def get_product(
    *,
    user: User,
    farm_id: int,
    product_id: int,
    allow_inactive: bool = False,
) -> Product:
    """Devuelve un insumo de la finca o 404."""
    membership = require_membership(user=user, farm_id=farm_id)
    require_action(membership, READ)
    queryset = _with_lent(
        Product.objects.filter(id=product_id, farm_id=farm_id, is_removed=False).select_related(
            "category",
            "unit",
            "unit__base_unit",
        )
    )
    if not allow_inactive:
        queryset = queryset.filter(is_active=True)
    product = queryset.first()
    if product is None:
        raise AppError("insumo no encontrado", 404)
    return product


def create_product(
    *,
    user: User,
    farm_id: int,
    name: str,
    category_code: str,
    unit_code: str,
    barcode: str = "",
    min_stock: Decimal = ZERO,
) -> Product:
    """Crea un insumo con catálogo del sistema o unidades custom activas."""
    membership = require_membership(user=user, farm_id=farm_id)
    require_action(membership, WRITE_STOCK)
    clean_name = _required_text(name, "el nombre del insumo es obligatorio", 100)
    clean_barcode = _optional_barcode(barcode)
    if min_stock < ZERO:
        raise AppError("el stock mínimo no puede ser negativo", 400)

    category = InventoryCategory.objects.filter(code=category_code, is_active=True).first()
    if category is None:
        raise AppError("categoría no válida", 400)
    unit = InventoryUnit.objects.filter(code=unit_code, is_active=True).first()
    if unit is None:
        raise AppError("unidad no válida", 400)
    if not CategoryUnit.objects.filter(category=category, unit=unit).exists():
        raise AppError("unidad no válida para esta categoría", 400)

    try:
        return Product.objects.create(
            farm=membership.farm,
            name=clean_name,
            barcode=clean_barcode,
            category=category,
            unit=unit,
            min_stock=min_stock,
            quantity=ZERO,
        )
    except IntegrityError as exc:
        raise AppError("ese código de barras ya existe en la finca", 409) from exc


def update_product(
    *,
    user: User,
    farm_id: int,
    product_id: int,
    name: str | None = None,
    barcode: str | None = None,
    category_code: str | None = None,
    unit_code: str | None = None,
    min_stock: Decimal | None = None,
) -> Product:
    """Edita datos del producto; cantidad no se toca aquí."""
    membership = require_membership(user=user, farm_id=farm_id)
    require_action(membership, MANAGE_PRODUCT)
    product = (
        Product.objects.filter(id=product_id, farm_id=farm_id, is_removed=False)
        .select_related("category", "unit")
        .first()
    )
    if product is None:
        raise AppError("insumo no encontrado", 404)

    if name is not None:
        product.name = _required_text(name, "el nombre del insumo es obligatorio", 100)
    if barcode is not None:
        product.barcode = _optional_barcode(barcode)
    if min_stock is not None:
        if min_stock < ZERO:
            raise AppError("el stock mínimo no puede ser negativo", 400)
        product.min_stock = min_stock
    if category_code is not None:
        category = InventoryCategory.objects.filter(
            code=category_code, is_active=True
        ).first()
        if category is None:
            raise AppError("categoría no válida", 400)
        product.category = category
    if unit_code is not None:
        unit = InventoryUnit.objects.filter(code=unit_code, is_active=True).first()
        if unit is None:
            raise AppError("unidad no válida", 400)
        product.unit = unit
    if not CategoryUnit.objects.filter(
        category=product.category, unit=product.unit
    ).exists():
        raise AppError("unidad no válida para esta categoría", 400)

    try:
        product.save()
    except IntegrityError as exc:
        raise AppError("ese código de barras ya existe en la finca", 409) from exc
    return product


def set_product_active(
    *,
    user: User,
    farm_id: int,
    product_id: int,
    is_active: bool,
) -> Product:
    """Activa o desactiva un producto sin borrar historial."""
    membership = require_membership(user=user, farm_id=farm_id)
    require_action(membership, MANAGE_PRODUCT)
    product = Product.objects.filter(id=product_id, farm_id=farm_id, is_removed=False).first()
    if product is None:
        raise AppError("insumo no encontrado", 404)
    product.is_active = is_active
    product.save(update_fields=["is_active", "updated_at"])
    return product


def delete_product(*, user: User, farm_id: int, product_id: int) -> None:
    """Quita el producto de la lista y conserva sus movimientos."""
    membership = require_membership(user=user, farm_id=farm_id)
    require_action(membership, MANAGE_PRODUCT)
    product = Product.objects.filter(id=product_id, farm_id=farm_id, is_removed=False).first()
    if product is None:
        raise AppError("insumo no encontrado", 404)
    product.is_removed = True
    product.is_active = False
    product.barcode = ""
    product.save(update_fields=["is_removed", "is_active", "barcode", "updated_at"])


def _apply_status(queryset: QuerySet[Product], *, status: str | None, include_inactive: bool):
    """Filtra activos, inactivos o todos."""
    if status == "all":
        return queryset
    if status == "inactive":
        return queryset.filter(is_active=False)
    if status == "active" or not include_inactive:
        return queryset.filter(is_active=True)
    return queryset


def _apply_order(queryset: QuerySet[Product], order: str) -> QuerySet[Product]:
    """Ordena por nombre, cantidad o último movimiento."""
    if order == "quantity":
        return queryset.order_by("quantity", "name", "id")
    if order == "last_movement":
        return queryset.order_by("-last_movement_at", "name", "id")
    return queryset.order_by("name", "id")


def _optional_barcode(value: str) -> str:
    cleaned = value.strip()
    if len(cleaned) > 64:
        raise AppError("el código de barras no puede superar los 64 caracteres", 400)
    return cleaned


def _with_lent(queryset: QuerySet[Product]) -> QuerySet[Product]:
    """Suma lo que sigue prestado de cada producto."""
    pending = (
        ProductLoan.objects.filter(product_id=OuterRef("pk"))
        .order_by()
        .values("product_id")
        .annotate(total=Sum(F("quantity") - F("returned_quantity")))
        .values("total")[:1]
    )
    return queryset.annotate(
        lent_quantity=Coalesce(
            Subquery(pending, output_field=DecimalField(max_digits=14, decimal_places=3)),
            Value(ZERO),
        )
    )


def _required_text(value: str, empty_message: str, max_length: int) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise AppError(empty_message, 400)
    if len(cleaned) > max_length:
        raise AppError(f"no puede superar los {max_length} caracteres", 400)
    return cleaned
