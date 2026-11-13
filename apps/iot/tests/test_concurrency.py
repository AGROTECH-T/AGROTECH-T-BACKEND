# ruff: noqa: S101
"""Concurrencia en escrituras IoT (IoT-02.2).

Propósito: dos administraciones simultáneas sobre la misma pareja o el mismo
canal producen un único resultado vigente; una desactivación de finca
concurrente impide terminar la escritura.
Contexto: ``transaction=True`` porque cada hilo usa su propia conexión y sus
transacciones deben confirmarse realmente. Solo mediante el orquestador
desechable.
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, current_thread

import pytest
from django.db import connections

from apps.accounts.errors import AppError
from apps.farms import services as farm_services
from apps.farms.services import deactivate_farm
from apps.iot import services as iot
from apps.iot.models import CoordinatorGateway, Gateway, Sensor

pytestmark = pytest.mark.django_db(transaction=True)


def _run_in_parallel(operation, workers=2):
    barrier = Barrier(workers)

    def worker():
        try:
            barrier.wait(timeout=5)
            return operation()
        except AppError as exc:
            return exc
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="iot") as pool:
        futures = [pool.submit(worker) for _ in range(workers)]
        return [future.result(timeout=20) for future in futures]


def test_parallel_authorizations_leave_one_active(admin, farm_id):
    gateway = iot.create_gateway(user=admin, farm_id=farm_id, name="G")
    coordinator = iot.create_coordinator(user=admin, farm_id=farm_id, name="C")

    results = _run_in_parallel(
        lambda: iot.authorize_gateway(
            user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid, gateway_uid=gateway.uid
        )
    )

    errors = [r for r in results if isinstance(r, AppError)]
    assert len(errors) == 1 and errors[0].status_code == 409
    assert CoordinatorGateway.objects.filter(active_pair=1).count() == 1


def test_parallel_sensors_on_same_channel_leave_one(admin, farm_id):
    coordinator = iot.create_coordinator(user=admin, farm_id=farm_id, name="C")

    results = _run_in_parallel(
        lambda: iot.create_sensor(
            user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid, channel=1,
            metric="temperature",
        )
    )

    errors = [r for r in results if isinstance(r, AppError)]
    assert len(errors) == 1 and errors[0].status_code == 409
    assert Sensor.objects.filter(coordinator__uid=coordinator.uid, is_active=True).count() == 1


def test_create_gateway_cannot_finish_after_farm_deactivation(admin, farm_id, monkeypatch):
    original_get = farm_services.get_membership
    ready, release = Event(), Event()

    def delayed_get(*, user, farm_id):
        result = original_get(user=user, farm_id=farm_id)
        if current_thread().name.startswith("writer"):
            ready.set()
            if not release.wait(timeout=5):
                raise RuntimeError("la escritura no fue liberada")
        return result

    monkeypatch.setattr(farm_services, "get_membership", delayed_get)

    def writer():
        try:
            return iot.create_gateway(user=admin, farm_id=farm_id, name="Tarde")
        except AppError as exc:
            return exc
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="writer") as pool:
        future = pool.submit(writer)
        assert ready.wait(timeout=5)
        try:
            deactivate_farm(user=admin, farm_id=farm_id)
        finally:
            release.set()
        result = future.result(timeout=10)

    assert isinstance(result, AppError) and result.status_code == 404
    assert not Gateway.objects.filter(farm_id=farm_id).exists()
