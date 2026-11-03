# ruff: noqa: S101
"""Pruebas de reglas de stock, barcode y catálogo.

@author Cristian Deysdayr Jimenez
"""
from decimal import Decimal

import pytest

from apps.accounts.errors import AppError
from apps.farms.services import create_farm
from apps.inventario import services
from apps.inventario.catalog import InventoryCategory, InventoryUnit
from apps.inventario.models import MovementKind


@pytest.mark.django_db
def test_create_product_requires_barcode(user):
    """Sin código de barras no se crea el insumo."""
    farm_id = create_farm(user=user, name="La Esperanza").farm_id
    with pytest.raises(AppError) as error:
        services.create_product(
            user=user,
            farm_id=farm_id,
            name="Fertilizante",
            barcode="   ",
            unit=InventoryUnit.KG,
            category=InventoryCategory.FERTILIZANTES,
        )
    assert error.value.status_code == 400


@pytest.mark.django_db
def test_create_product_rejects_invalid_unit(user):
    """Una unidad fuera del catálogo se rechaza."""
    farm_id = create_farm(user=user, name="La Esperanza").farm_id
    with pytest.raises(AppError) as error:
        services.create_product(
            user=user,
            farm_id=farm_id,
            name="Concentrado",
            barcode="770001",
            unit="tonelada",
            category=InventoryCategory.CONCENTRADOS,
        )
    assert error.value.status_code == 400


@pytest.mark.django_db
def test_barcode_unique_per_farm(user):
    """El mismo código de barras no se repite en una finca."""
    farm_id = create_farm(user=user, name="La Esperanza").farm_id
    services.create_product(
        user=user,
        farm_id=farm_id,
        name="NPK",
        barcode="770123",
        unit=InventoryUnit.KG,
        category=InventoryCategory.FERTILIZANTES,
    )
    with pytest.raises(AppError) as error:
        services.create_product(
            user=user,
            farm_id=farm_id,
            name="Otro",
            barcode="770123",
            unit=InventoryUnit.KG,
            category=InventoryCategory.FERTILIZANTES,
        )
    assert error.value.status_code == 409


@pytest.mark.django_db
def test_salida_no_deja_stock_negativo(user):
    """Una salida mayor al stock se rechaza."""
    farm_id = create_farm(user=user, name="La Esperanza").farm_id
    product = services.create_product(
        user=user,
        farm_id=farm_id,
        name="Concentrado inicio",
        barcode="770999",
        unit=InventoryUnit.BULTO_40KG,
        category=InventoryCategory.CONCENTRADOS,
    )
    services.register_movement(
        user=user,
        farm_id=farm_id,
        product_id=product.id,
        kind=MovementKind.ENTRADA,
        quantity=Decimal("2"),
    )
    with pytest.raises(AppError) as error:
        services.register_movement(
            user=user,
            farm_id=farm_id,
            product_id=product.id,
            kind=MovementKind.SALIDA,
            quantity=Decimal("5"),
        )
    assert error.value.status_code == 400
