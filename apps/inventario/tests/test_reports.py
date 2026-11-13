# ruff: noqa: S101
"""Pruebas de los reportes de inventario.

@author Cristian Deysdayr Jimenez
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest

from apps.accounts.errors import AppError
from apps.farms.models import FarmMembership, FarmRole
from apps.farms.services import create_farm
from apps.inventario import services
from apps.inventario.models import MovementKind
from apps.inventario.seed_catalog import seed_catalog
from apps.inventario.services import loans as loan_services
from apps.inventario.services import reports as report_services


@pytest.fixture(autouse=True)
def _catalog(db):
    seed_catalog()


def _farm_product(user, name="Semilla"):
    farm_id = create_farm(user=user, name="La Esperanza").farm_id
    product = services.create_product(
        user=user,
        farm_id=farm_id,
        name=name,
        unit_code="kg",
        category_code="semilla",
        min_stock=Decimal("5"),
    )
    return farm_id, product


@pytest.mark.django_db
def test_reporte_de_ajustes_filtra_por_motivo(user):
    """Lista el reconteo y omite los de otro motivo."""
    farm_id, product = _farm_product(user)
    services.register_movement(
        user=user,
        farm_id=farm_id,
        product_id=product.id,
        kind=MovementKind.ENTRADA,
        quantity=Decimal("10"),
    )
    services.register_movement(
        user=user,
        farm_id=farm_id,
        product_id=product.id,
        kind=MovementKind.AJUSTE,
        quantity=Decimal("8"),
        note="faltaban dos",
        reason="conteo_fisico",
    )
    data = report_services.adjustment_report(
        user=user,
        farm_id=farm_id,
        reason="conteo_fisico",
    )
    assert data["count"] == 1
    assert data["rows"][0]["reason"] == "conteo_fisico"
    empty = report_services.adjustment_report(
        user=user,
        farm_id=farm_id,
        reason="vencimiento",
    )
    assert empty["count"] == 0


@pytest.mark.django_db
def test_reporte_de_ajustes_rechaza_motivo_invalido(user):
    """Un código de motivo ajeno no se consulta."""
    farm_id, _product = _farm_product(user)
    with pytest.raises(AppError) as error:
        report_services.adjustment_report(user=user, farm_id=farm_id, reason="no_existe")
    assert error.value.status_code == 400


@pytest.mark.django_db
def test_reporte_de_consumo_suma_salidas(user):
    """El consumo cuenta las salidas del producto."""
    farm_id, product = _farm_product(user)
    services.register_movement(
        user=user,
        farm_id=farm_id,
        product_id=product.id,
        kind=MovementKind.ENTRADA,
        quantity=Decimal("10"),
    )
    services.register_movement(
        user=user,
        farm_id=farm_id,
        product_id=product.id,
        kind=MovementKind.SALIDA,
        quantity=Decimal("3"),
        note="lote 1",
    )
    data = report_services.consumption_report(user=user, farm_id=farm_id)
    assert data["rows"][0]["product"] == "Semilla"
    assert data["rows"][0]["moves"] == 1


@pytest.mark.django_db
def test_reporte_de_consumo_rechaza_rango_invertido(user):
    """La fecha inicial no puede ir después de la final."""
    farm_id, _product = _farm_product(user)
    with pytest.raises(AppError) as error:
        report_services.consumption_report(
            user=user,
            farm_id=farm_id,
            date_from=date.today(),
            date_to=date.today() - timedelta(days=1),
        )
    assert error.value.status_code == 400


@pytest.mark.django_db
def test_reporte_de_prestamos_separa_pendientes(user):
    """Cuenta abiertos y cerrados del préstamo."""
    farm_id, product = _farm_product(user, name="Concentrado")
    product = services.create_product(
        user=user,
        farm_id=farm_id,
        name="Bulto",
        unit_code="bulto_40kg",
        category_code="alimento_concentrado",
    )
    services.register_movement(
        user=user,
        farm_id=farm_id,
        product_id=product.id,
        kind=MovementKind.ENTRADA,
        quantity=Decimal("4"),
    )
    loan_services.lend_product(
        user=user,
        farm_id=farm_id,
        product_id=product.id,
        borrower_name="Ana Pérez",
        document_number="1234567890",
        quantity=Decimal("1"),
    )
    data = report_services.loan_report(user=user, farm_id=farm_id, status="open")
    assert data["open"] == 1
    assert data["count"] == 1


@pytest.mark.django_db
def test_reporte_de_prestamos_rechaza_estado_invalido(user):
    """Solo admite all, open o closed."""
    farm_id, _product = _farm_product(user)
    with pytest.raises(AppError) as error:
        report_services.loan_report(user=user, farm_id=farm_id, status="raro")
    assert error.value.status_code == 400


@pytest.mark.django_db
def test_reporte_de_pedido_marca_stock_bajo(user):
    """Si el saldo está en el mínimo, el pedido lo incluye."""
    farm_id, product = _farm_product(user)
    services.register_movement(
        user=user,
        farm_id=farm_id,
        product_id=product.id,
        kind=MovementKind.ENTRADA,
        quantity=Decimal("5"),
    )
    data = report_services.order_report(user=user, farm_id=farm_id, only_low=True)
    names = [row["product"] for row in data["rows"]]
    assert product.name in names


@pytest.mark.django_db
def test_consulta_puede_leer_reportes(user, make_user):
    """Quien solo consulta ve el reporte; no hace falta ser admin."""
    farm_id, _product = _farm_product(user)
    viewer = make_user(identification="33445566")
    FarmMembership.objects.create(farm_id=farm_id, user=viewer, role=FarmRole.CONSULTA)
    data = report_services.adjustment_report(user=viewer, farm_id=farm_id)
    assert data["count"] == 0
