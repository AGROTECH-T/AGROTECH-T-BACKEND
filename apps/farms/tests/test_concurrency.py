# ruff: noqa: S101
"""Pruebas de concurrencia de la baja lógica de fincas."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, current_thread

import pytest
from django.db import connection, connections
from django.db.models.signals import post_save

from apps.accounts.errors import AppError
from apps.farms import services
from apps.farms.models import Farm, FarmMembership, FarmRole
from apps.trazabilidad.models import AuditEvent, EventType

pytestmark = pytest.mark.django_db(transaction=True)


def _run_concurrently(*operations):
    """Ejecuta operaciones en hilos con conexiones independientes."""
    barrier = Barrier(len(operations))

    def run(operation):
        barrier.wait(timeout=5)
        try:
            return operation()
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=len(operations)) as pool:
        return list(pool.map(run, operations))


def test_concurrent_deactivation_keeps_single_audit(make_user):
    """Dos bajas simultáneas registran una sola fecha y responsable."""
    first = make_user("11111111")
    second = make_user("22222222")
    membership = services.create_farm(user=first, name="Concurrente")
    farm_id = membership.farm_id
    FarmMembership.objects.create(
        farm_id=farm_id,
        user=second,
        role=FarmRole.ADMINISTRADOR,
    )
    saved = []

    def capture(sender, instance, **kwargs):
        if instance.pk == farm_id:
            saved.append(instance.deactivated_by_id)

    post_save.connect(capture, sender=Farm, weak=False)
    try:
        _run_concurrently(
            lambda: services.deactivate_farm(user=first, farm_id=farm_id),
            lambda: services.deactivate_farm(user=second, farm_id=farm_id),
        )
    finally:
        post_save.disconnect(capture, sender=Farm)

    farm = Farm.objects.get(pk=farm_id)
    assert farm.is_active is False
    assert farm.deactivated_at is not None
    assert len(saved) == 1
    assert saved[0] == farm.deactivated_by_id
    assert farm.deactivated_by_id in {first.pk, second.pk}

    other = second if farm.deactivated_by_id == first.pk else first
    original_at = farm.deactivated_at
    services.deactivate_farm(user=other, farm_id=farm_id)
    farm.refresh_from_db()
    assert farm.deactivated_at == original_at
    assert farm.deactivated_by_id == saved[0]


def test_concurrent_reactivation_records_single_event(make_user):
    """Dos reactivaciones simultáneas dejan un solo evento y estado consistente."""
    first = make_user("11111111")
    second = make_user("22222222")
    membership = services.create_farm(user=first, name="Reactivable")
    farm_id = membership.farm_id
    FarmMembership.objects.create(
        farm_id=farm_id,
        user=second,
        role=FarmRole.ADMINISTRADOR,
    )
    services.deactivate_farm(user=first, farm_id=farm_id)

    _run_concurrently(
        lambda: services.reactivate_farm(user=first, farm_id=farm_id),
        lambda: services.reactivate_farm(user=second, farm_id=farm_id),
    )

    farm = Farm.objects.get(pk=farm_id)
    assert farm.is_active is True
    assert farm.deactivated_at is None
    assert farm.deactivated_by_id is None
    count = AuditEvent.objects.filter(
        farm_id=farm_id,
        event_type=EventType.FARM_REACTIVATED,
    ).count()
    assert count == 1


def test_update_cannot_finish_after_deactivation(make_user, monkeypatch):
    """Una actualización que espera tras la comprobación no escribe."""
    admin = make_user("11111111")
    membership = services.create_farm(user=admin, name="Original")
    farm_id = membership.farm_id
    original_get = services.get_membership
    ready = Event()
    release = Event()

    def delayed_get(*, user, farm_id):
        result = original_get(user=user, farm_id=farm_id)
        if current_thread().name.startswith("writer"):
            ready.set()
            if not release.wait(timeout=5):
                raise RuntimeError("la escritura no fue liberada")
        return result

    monkeypatch.setattr(services, "get_membership", delayed_get)

    def writer():
        try:
            return services.update_farm(
                user=admin,
                farm_id=farm_id,
                name="Nuevo",
            )
        except AppError as exc:
            return exc
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="writer") as pool:
        future = pool.submit(writer)
        assert ready.wait(timeout=5)
        try:
            services.deactivate_farm(user=admin, farm_id=farm_id)
        finally:
            release.set()
        result = future.result(timeout=10)

    assert isinstance(result, AppError)
    assert result.status_code == 404
    farm = Farm.objects.get(pk=farm_id)
    assert farm.is_active is False
    assert farm.name == "Original"


def test_deactivation_waits_for_inflight_update(make_user, monkeypatch):
    """Una baja no confirma mientras una escritura en curso retiene el bloqueo.

    Demuestra la exclusión mutua de forma determinista mediante coordinación de
    eventos entre transacciones reales en conexiones independientes: la escritura
    obtiene el bloqueo de ``Farm`` dentro de ``transaction.atomic()``; la
    desactivación, en otra conexión, alcanza el punto de adquisición y no logra
    adquirir el bloqueo mientras la escritura lo retiene. No depende de
    ``information_schema.innodb_trx`` (que exigiría el privilegio global
    ``PROCESS``): la espera se comprueba con una señal previa a la adquisición y
    una espera acotada sobre el evento de adquisición.
    """
    admin = make_user("11111111")
    membership = services.create_farm(user=admin, name="Original")
    farm_id = membership.farm_id
    original_lock = services._lock_farm
    writer_locked = Event()
    release_writer = Event()
    writer_finished = Event()
    deactivation_attempted = Event()
    deactivation_acquired = Event()
    deactivation_finished = Event()
    writer_state = {}

    def instrumented_lock(*, farm_id):
        thread_name = current_thread().name
        if thread_name.startswith("writer"):
            farm = original_lock(farm_id=farm_id)
            writer_state["in_atomic_block"] = connection.in_atomic_block
            writer_locked.set()
            if not release_writer.wait(timeout=5):
                raise RuntimeError("la escritura no fue liberada")
            return farm
        if thread_name.startswith("deactivator"):
            deactivation_attempted.set()
            farm = original_lock(farm_id=farm_id)
            deactivation_acquired.set()
            return farm
        return original_lock(farm_id=farm_id)

    monkeypatch.setattr(services, "_lock_farm", instrumented_lock)

    def writer():
        try:
            return services.update_farm(
                user=admin,
                farm_id=farm_id,
                name="Nuevo",
            )
        except AppError as exc:
            return exc
        finally:
            writer_finished.set()
            connections.close_all()

    def deactivator():
        try:
            return services.deactivate_farm(user=admin, farm_id=farm_id)
        finally:
            deactivation_finished.set()
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="writer") as writers:
        writer_future = writers.submit(writer)
        assert writer_locked.wait(timeout=5)
        assert writer_state.get("in_atomic_block") is True
        with ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="deactivator",
        ) as deactivators:
            deactivation_future = deactivators.submit(deactivator)
            try:
                # Señal verificable: el desactivador llegó al punto de adquisición
                # del bloqueo (justo antes del SELECT ... FOR UPDATE).
                assert deactivation_attempted.wait(timeout=5)
                # El escritor aún conserva el bloqueo dentro de su transacción.
                assert not writer_finished.is_set()
                # Exclusión mutua determinista: mientras la escritura retiene el
                # bloqueo, la desactivación no adquiere la fila durante una
                # ventana acotada y no puede completar.
                assert deactivation_acquired.wait(timeout=0.2) is False
                assert not deactivation_finished.is_set()
                assert deactivation_future.running()
                assert not deactivation_future.done()
            finally:
                # Liberar la transacción escritora para que confirme y libere el bloqueo.
                release_writer.set()

            update_result = writer_future.result(timeout=10)
            deactivation_result = deactivation_future.result(timeout=10)

    assert not isinstance(update_result, AppError)
    assert isinstance(deactivation_result, Farm)
    assert deactivation_acquired.is_set()
    assert deactivation_finished.is_set()
    farm = Farm.objects.get(pk=farm_id)
    assert farm.is_active is False
    assert farm.name == "Nuevo"
    assert farm.deactivated_by_id == admin.pk
