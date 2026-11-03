"""Casos de uso de insumos de inventario.

Propósito: listar, consultar y crear insumos por finca.
Contexto: unidad y categoría salen del catálogo del sistema.
@author Cristian Deysdayr Jimenez
"""
from decimal import Decimal

from django.db import IntegrityError

from apps.accounts.errors import AppError
from apps.accounts.models import User
from apps.inventario.catalog import InventoryCategory, InventoryUnit
from apps.inventario.models import Product
from apps.inventario.services.membership import require_membership

ZERO = Decimal("0")


def list_products(*, user: User, farm_id: int):
    """Lista insumos activos de una finca del usuario."""
    require_membership(user=user, farm_id=farm_id)
    return Product.objects.filter(farm_id=farm_id, is_active=True)


def get_product(*, user: User, farm_id: int, product_id: int) -> Product:
    """Devuelve un insumo de la finca o 404."""
    require_membership(user=user, farm_id=farm_id)
    product = Product.objects.filter(
        id=product_id,
        farm_id=farm_id,
        is_active=True,
    ).first()
    if product is None:
        raise AppError("insumo no encontrado", 404)
    return product


def create_product(
    *,
    user: User,
    farm_id: int,
    name: str,
    barcode: str,
    unit: str,
    category: str,
    min_stock: Decimal = ZERO,
) -> Product:
    """Crea un insumo con código de barras y catálogo del sistema."""
    membership = require_membership(user=user, farm_id=farm_id)
    clean_name = _required_text(name, "el nombre del insumo es obligatorio", 100)
    clean_barcode = _required_text(barcode, "el código de barras es obligatorio", 64)
    if unit not in InventoryUnit.values:
        raise AppError("unidad no válida", 400)
    if category not in InventoryCategory.values:
        raise AppError("categoría no válida", 400)
    if min_stock < ZERO:
        raise AppError("el stock mínimo no puede ser negativo", 400)

    try:
        return Product.objects.create(
            farm=membership.farm,
            name=clean_name,
            barcode=clean_barcode,
            unit=unit,
            category=category,
            min_stock=min_stock,
            quantity=ZERO,
        )
    except IntegrityError as exc:
        raise AppError("ese código de barras ya existe en la finca", 409) from exc


def _required_text(value: str, empty_message: str, max_length: int) -> str:
    """Normaliza un texto obligatorio."""
    cleaned = value.strip()
    if not cleaned:
        raise AppError(empty_message, 400)
    if len(cleaned) > max_length:
        raise AppError(f"no puede superar los {max_length} caracteres", 400)
    return cleaned
