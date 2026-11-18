# ruff: noqa: S101
"""Ventana anti-replay por arranque de coordinador (IoT-03.2).

Propósito: aceptar el arranque mayor y los tres anteriores, aceptar arranques y
secuencias desordenados dentro de la ventana, rechazar arranques fuera de ella y
no dejar la ventana avanzada si la transacción del llamador falla. La
deduplicación de ``seq`` y la persistencia de mediciones son de IoT-04.
Contexto: ``transaction=True`` para controlar la transacción del llamador.
Solo mediante el orquestador desechable.
"""
import pytest
from django.db import transaction

from apps.accounts.errors import AppError
from apps.iot import services as iot
from apps.iot.models import BOOT_WINDOW_SIZE, CoordinatorBootWindow
from apps.iot.services.coordinator_credentials import (
    AuthenticatedCoordinatorFrame,
    SequenceOutOfWindow,
)

pytestmark = pytest.mark.django_db(transaction=True)


def _frame(coordinator, farm_id, boot, seq=1) -> AuthenticatedCoordinatorFrame:
    return AuthenticatedCoordinatorFrame(
        coordinator_id=coordinator.pk,
        coordinator_uid=coordinator.uid,
        farm_id=farm_id,
        credential_id=1,
        boot_counter=boot,
        seq=seq,
        key_version=1,
    )


def _accept(coordinator, farm_id, boot, seq=1):
    with transaction.atomic():
        iot.accept_coordinator_sequence(frame=_frame(coordinator, farm_id, boot, seq))


def _boots(coordinator):
    return list(
        CoordinatorBootWindow.objects.filter(coordinator=coordinator)
        .order_by("boot_counter")
        .values_list("boot_counter", flat=True)
    )


def test_window_size_is_four():
    assert BOOT_WINDOW_SIZE == 4


def test_requires_an_atomic_transaction(admin, farm_id):
    coordinator = iot.create_coordinator(user=admin, farm_id=farm_id, name="C")
    with pytest.raises(RuntimeError, match="transacción"):
        iot.accept_coordinator_sequence(frame=_frame(coordinator, farm_id, 1))


def test_first_boot_opens_the_window_at_any_value(admin, farm_id):
    coordinator = iot.create_coordinator(user=admin, farm_id=farm_id, name="C")
    _accept(coordinator, farm_id, 100)
    assert _boots(coordinator) == [100]


def test_newer_boots_slide_the_window_and_prune_older_ones(admin, farm_id):
    coordinator = iot.create_coordinator(user=admin, farm_id=farm_id, name="C")
    for boot in range(5):
        _accept(coordinator, farm_id, boot)
    assert _boots(coordinator) == [1, 2, 3, 4]


def test_late_boot_inside_the_window_is_accepted_and_refreshed(admin, farm_id):
    coordinator = iot.create_coordinator(user=admin, farm_id=farm_id, name="C")
    for boot in range(5):
        _accept(coordinator, farm_id, boot)
    _accept(coordinator, farm_id, 2)
    assert _boots(coordinator) == [1, 2, 3, 4]
    row = CoordinatorBootWindow.objects.get(coordinator=coordinator, boot_counter=2)
    assert row.last_seen_at >= row.first_seen_at


def test_boot_below_the_window_is_rejected(admin, farm_id):
    coordinator = iot.create_coordinator(user=admin, farm_id=farm_id, name="C")
    for boot in range(5):
        _accept(coordinator, farm_id, boot)
    with pytest.raises(SequenceOutOfWindow):
        _accept(coordinator, farm_id, 0)
    _accept(coordinator, farm_id, 1)
    assert _boots(coordinator) == [1, 2, 3, 4]


def test_duplicate_boot_creates_a_single_row(admin, farm_id):
    coordinator = iot.create_coordinator(user=admin, farm_id=farm_id, name="C")
    _accept(coordinator, farm_id, 7, seq=3)
    _accept(coordinator, farm_id, 7, seq=1)
    assert _boots(coordinator) == [7]


def test_out_of_order_seq_within_a_boot_is_accepted(admin, farm_id):
    coordinator = iot.create_coordinator(user=admin, farm_id=farm_id, name="C")
    _accept(coordinator, farm_id, 3, seq=10)
    _accept(coordinator, farm_id, 3, seq=1)
    assert _boots(coordinator) == [3]


def test_window_is_per_coordinator(admin, farm_id):
    first = iot.create_coordinator(user=admin, farm_id=farm_id, name="C1")
    second = iot.create_coordinator(user=admin, farm_id=farm_id, name="C2")
    _accept(first, farm_id, 5)
    _accept(second, farm_id, 9)
    assert _boots(first) == [5]
    assert _boots(second) == [9]


@pytest.mark.parametrize("boot", [True, False])
def test_boolean_boot_counter_is_rejected(admin, farm_id, boot):
    coordinator = iot.create_coordinator(user=admin, farm_id=farm_id, name="C")
    with transaction.atomic():
        with pytest.raises(TypeError):
            iot.accept_coordinator_sequence(frame=_frame(coordinator, farm_id, boot))


def test_negative_boot_counter_is_rejected(admin, farm_id):
    coordinator = iot.create_coordinator(user=admin, farm_id=farm_id, name="C")
    with transaction.atomic():
        with pytest.raises(ValueError):
            iot.accept_coordinator_sequence(frame=_frame(coordinator, farm_id, -1))


def test_non_authenticated_frame_is_rejected(admin, farm_id):
    with transaction.atomic():
        with pytest.raises(TypeError):
            iot.accept_coordinator_sequence(frame={"boot_counter": 1})


def test_missing_coordinator_is_not_found(admin, farm_id):
    coordinator = iot.create_coordinator(user=admin, farm_id=farm_id, name="C")
    frame = AuthenticatedCoordinatorFrame(
        coordinator_id=coordinator.pk + 999,
        coordinator_uid=coordinator.uid,
        farm_id=farm_id,
        credential_id=1,
        boot_counter=1,
        seq=1,
        key_version=1,
    )
    with transaction.atomic():
        with pytest.raises(AppError) as excinfo:
            iot.accept_coordinator_sequence(frame=frame)
    assert excinfo.value.status_code == 404


def test_window_rolls_back_with_the_caller_transaction(admin, farm_id):
    coordinator = iot.create_coordinator(user=admin, farm_id=farm_id, name="C")
    with pytest.raises(RuntimeError):
        with transaction.atomic():
            iot.accept_coordinator_sequence(frame=_frame(coordinator, farm_id, 4))
            raise RuntimeError("fallo al persistir la medición")
    assert _boots(coordinator) == []