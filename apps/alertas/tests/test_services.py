# ruff: noqa: S101
"""Avisos de stock bajo y agotado.

@author Cristian Deysdayr Jimenez
"""
from decimal import Decimal

import pytest

from apps.alertas import services as alert_services
from apps.farms.services import create_farm
from apps.inventario import services
from apps.inventario.models import MovementKind


@pytest.mark.django_db
def test_lista_aviso_cuando_el_stock_esta_bajo(user):
    """Si el saldo llega al mínimo, sale en la campana."""
    farm_id = create_farm(user=user, name="La Esperanza").farm_id
    product = services.create_product(
        user=user,
        farm_id=farm_id,
        name="Urea",
        unit_code="kg",
        category_code="fertilizante_abono",
        min_stock=Decimal("4"),
    )
    services.register_movement(
        user=user,
        farm_id=farm_id,
        product_id=product.id,
        kind=MovementKind.ENTRADA,
        quantity=Decimal("4"),
    )
    items = alert_services.list_alerts(user=user)
    assert len(items) == 1
    assert items[0]["kind"] == "bajo"
    assert items[0]["product"] == "Urea"


@pytest.mark.django_db
def test_marca_agotado_cuando_no_queda_saldo(user):
    """Saldo en cero con mínimo mayor a cero es agotado."""
    farm_id = create_farm(user=user, name="La Esperanza").farm_id
    services.create_product(
        user=user,
        farm_id=farm_id,
        name="Urea",
        unit_code="kg",
        category_code="fertilizante_abono",
        min_stock=Decimal("2"),
    )
    items = alert_services.list_alerts(user=user)
    assert items[0]["kind"] == "agotado"


@pytest.mark.django_db
def test_sin_finca_no_hay_avisos(make_user):
    """Quien no pertenece a ninguna finca no ve avisos."""
    stranger = make_user(identification="99887766")
    assert alert_services.list_alerts(user=stranger) == []
