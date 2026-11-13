# ruff: noqa: S101
"""Pruebas de concurrencia entre inventario y baja de fincas."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Event, current_thread

import pytest
from django.db import connections

from apps.accounts.errors import AppError
from apps.farms import services as farm_services
from apps.farms.services import create_farm, deactivate_farm
from apps.inventario import services
from apps.inventario.catalog import InventoryCategory, InventoryUnit
from apps.inventario.models import Movement, MovementKind, Product

pytestmark = pytest.mark.django_db(transaction=True)


def _delay_writer_after_membership_check(monkeypatch):
    """Pausa al escritor después de la lectura previa de membresía."""
    original_get = farm_services.get_membership
    ready = Event()
    release = Event()

    def delayed_get(*, user, farm_id):
        result = original_get(user=user, farm_id=farm_id)
        if current_thread().name.startswith("writer"):
            ready.set()
            if not release.wait(timeout=5):
                raise RuntimeError("la escritura no fue liberada")
        return result

    monkeypatch.setattr(farm_services, "get_membership", delayed_get)
    return ready, release


def test_create_product_cannot_finish_after_deactivation(make_user, monkeypatch):
    """Crear un producto no puede completarse tras la desactivación."""
    owner = make_user("11111111")
    farm_id = create_farm(user=owner, name="Concurrente").farm_id
    ready, release = _delay_writer_after_membership_check(monkeypatch)

    def writer():
        try:
            return services.create_product(
                user=owner,
                farm_id=farm_id,
                name="Fertilizante",
                barcode="770001",
                unit=InventoryUnit.KG,
                category=InventoryCategory.FERTILIZANTES,
            )
        except AppError as exc:
            return exc
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="writer") as pool:
        future = pool.submit(writer)
        assert ready.wait(timeout=5)
        try:
            deactivate_farm(user=owner, farm_id=farm_id)
        finally:
            release.set()
        result = future.result(timeout=10)

    assert isinstance(result, AppError)
    assert result.status_code == 404
    assert not Product.objects.filter(farm_id=farm_id).exists()


def test_register_movement_cannot_finish_after_deactivation(make_user, monkeypatch):
    """Registrar un movimiento no puede completarse tras la desactivación."""
    owner = make_user("11111111")
    farm_id = create_farm(user=owner, name="Concurrente").farm_id
    product = services.create_product(
        user=owner,
        farm_id=farm_id,
        name="Fertilizante",
        barcode="770001",
        unit=InventoryUnit.KG,
        category=InventoryCategory.FERTILIZANTES,
    )
    ready, release = _delay_writer_after_membership_check(monkeypatch)

    def writer():
        try:
            return services.register_movement(
                user=owner,
                farm_id=farm_id,
                product_id=product.id,
                kind=MovementKind.ENTRADA,
                quantity=Decimal("5"),
            )
        except AppError as exc:
            return exc
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="writer") as pool:
        future = pool.submit(writer)
        assert ready.wait(timeout=5)
        try:
            deactivate_farm(user=owner, farm_id=farm_id)
        finally:
            release.set()
        result = future.result(timeout=10)

    assert isinstance(result, AppError)
    assert result.status_code == 404
    product.refresh_from_db()
    assert product.quantity == Decimal("0")
    assert not Movement.objects.filter(product=product).exists()
