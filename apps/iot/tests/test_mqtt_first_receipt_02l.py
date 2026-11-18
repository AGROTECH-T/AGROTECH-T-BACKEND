# ruff: noqa: S101, S105, S106, F811
"""BUILD-FLEPSI-02L: primera recepción MQTT concurrente.

Contra MariaDB 11.8 desechable con transacciones reales en hilos. Las
aserciones exigen 0 PERSISTENCE_ERROR y, si aparece alguno, muestran el
``errno`` real de MariaDB registrado por el servicio (nunca el texto).
"""

from __future__ import annotations

import base64
import logging
import re
import secrets
import threading

import pytest
from django.db import IntegrityError, OperationalError, connections, transaction
from django.db.transaction import TransactionManagementError

import apps.iot.services.mqtt_delivery_receipts as receipts
from apps.iot.broker_identity_config import build_keyring
from apps.iot.models import IotMqttDelivery, IotMqttDeliveryEvent
from apps.iot.mqtt.broker_identity import BrokerIdentityObservation
from apps.iot.mqtt.payload import MAX_MESSAGE_BYTES
from apps.iot.services.mqtt_delivery_receipts import MqttReceiptStatus
from apps.iot.services.mqtt_payload_crypto import register_encrypted_mqtt_delivery
from apps.iot.tests.mqtt_delivery_support import (  # noqa: F401 (fixtures)
    frame_for,
    mqtt_key_settings,
    raw_payload,
    world,
)
from apps.iot.tests.test_broker_identity_concurrency import _parallel

pytestmark = pytest.mark.django_db(transaction=True)

RECEIVERS = (3, 4, 8, 16)
REPETITIONS = 5
ERRNO_RE = re.compile(r"errno=(\S+)")


@pytest.fixture
def kr():
    key = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
    return build_keyring(raw_key=key, raw_version="1", raw_previous=None, raw_next=None)


def obs(keyring, value):
    return BrokerIdentityObservation.from_user_properties([("token_id", value)], keyring)


def receive(world, identity, payload):
    return register_encrypted_mqtt_delivery(
        topic=world.topic,
        payload=payload,
        qos=1,
        max_payload_bytes=MAX_MESSAGE_BYTES,
        coordinator_id=world.coordinator.pk,
        broker_identity=identity,
    )


def committed_rows(transport_key):
    """Cuenta filas desde otra conexión: solo ve datos confirmados."""
    box = {}

    def count():
        try:
            box["n"] = IotMqttDelivery.objects.filter(transport_key=transport_key).count()
        finally:
            connections.close_all()

    thread = threading.Thread(target=count)
    thread.start()
    thread.join(timeout=30)
    return box["n"]


def logged_errnos(caplog):
    return [
        match.group(1)
        for record in caplog.records
        if record.name == receipts.__name__
        for match in [ERRNO_RE.search(record.getMessage())]
        if match
    ]


def _identities(mode, kr, receivers, other_value="A"):
    if mode == "off":
        return [None] * receivers
    return [obs(kr, "A")] + [obs(kr, other_value) for _ in range(receivers - 1)]


@pytest.mark.parametrize("mode", ["off", "record"])
@pytest.mark.parametrize("receivers", RECEIVERS)
def test_concurrent_first_receipt_matrix(world, kr, caplog, mode, receivers):
    caplog.set_level(logging.WARNING, logger=receipts.__name__)
    for repetition in range(REPETITIONS):
        payload = raw_payload(frame_for(world, seq=1000 + receivers * 10 + repetition))
        identities = _identities(mode, kr, receivers)
        results = _parallel([lambda i=i, p=payload: receive(world, i, p) for i in identities])
        errors = [r for r in results if isinstance(r, Exception)]
        statuses = [getattr(r, "status", None) for r in results]
        failed = statuses.count(MqttReceiptStatus.PERSISTENCE_ERROR)
        assert not errors and failed == 0, (
            f"rep={repetition} statuses={[getattr(s, 'value', s) for s in statuses]} "
            f"errnos={logged_errnos(caplog)} errors={[type(e).__name__ for e in errors]}"
        )
        assert statuses.count(MqttReceiptStatus.PERSISTED) == 1
        assert statuses.count(MqttReceiptStatus.REDELIVERY) == receivers - 1
        delivery = results[0].delivery
        assert {r.delivery.pk for r in results} == {delivery.pk}
        assert committed_rows(delivery.transport_key) == 1  # confirmada al devolver
        events = IotMqttDeliveryEvent.objects.filter(delivery_id=delivery.pk)
        assert events.filter(event_type="RECEIVED").count() == 1
        assert events.filter(event_type="REDELIVERY").count() == receivers - 1
        assert events.filter(dedupe_key__startswith="bi:").count() == 0
        stored = IotMqttDelivery.objects.get(pk=delivery.pk)
        if mode == "off":
            assert stored.broker_identity_presence is None
        else:
            assert stored.broker_identity_presence is not None
    assert logged_errnos(caplog) == []


@pytest.mark.parametrize("receivers", RECEIVERS)
def test_concurrent_first_receipt_record_mismatch_is_recorded_once(world, kr, caplog, receivers):
    caplog.set_level(logging.WARNING, logger=receipts.__name__)
    for repetition in range(2):
        payload = raw_payload(frame_for(world, seq=2000 + receivers * 10 + repetition))
        identities = [obs(kr, "A")] * (receivers // 2) + [obs(kr, "B")] * (
            receivers - receivers // 2
        )
        results = _parallel([lambda i=i, p=payload: receive(world, i, p) for i in identities])
        statuses = [getattr(r, "status", r) for r in results]
        assert MqttReceiptStatus.PERSISTENCE_ERROR not in statuses, (
            f"statuses={statuses} errnos={logged_errnos(caplog)}"
        )
        assert statuses.count(MqttReceiptStatus.PERSISTED) == 1
        delivery = results[0].delivery
        mismatches = IotMqttDeliveryEvent.objects.filter(
            delivery_id=delivery.pk, dedupe_key__startswith="bi:mismatch:"
        ).count()
        assert mismatches == 1  # la evidencia distinta de la ganadora, una sola vez


# -- Reintentos acotados ante 1213 y recuperación de colisión ------------------------


@pytest.fixture
def payload(world):
    return raw_payload(frame_for(world, seq=3000))


def _fail_event_create(monkeypatch, errors):
    """El primer ``RECEIVED`` falla con los errores dados (tras insertar la entrega)."""
    real = IotMqttDeliveryEvent.objects.create
    pending = list(errors)

    def create(**kwargs):
        if kwargs.get("event_type") == "RECEIVED" and pending:
            raise pending.pop(0)
        return real(**kwargs)

    monkeypatch.setattr(IotMqttDeliveryEvent.objects, "create", create)


def _spy_attempts(monkeypatch):
    """Registra si cada intento empieza fuera de cualquier transacción."""
    real = receipts._register_attempt
    seen = []

    def attempt(**kwargs):
        seen.append(transaction.get_connection().in_atomic_block)
        return real(**kwargs)

    monkeypatch.setattr(receipts, "_register_attempt", attempt)
    monkeypatch.setattr(receipts, "_deadlock_pause", lambda _n: None)
    return seen


def _deadlock():
    return OperationalError(receipts.MARIADB_DEADLOCK, "Deadlock found")


@pytest.mark.parametrize("mode", ["off", "record"])
def test_deadlock_is_retried_in_a_new_transaction(world, kr, payload, monkeypatch, mode):
    seen = _spy_attempts(monkeypatch)
    _fail_event_create(monkeypatch, [_deadlock()])
    identity = None if mode == "off" else obs(kr, "A")
    result = receive(world, identity, payload)
    assert result.status is MqttReceiptStatus.PERSISTED
    assert seen == [False, False]  # dos intentos, cada uno en transacción nueva
    assert IotMqttDelivery.objects.count() == 1  # el primer INSERT se revirtió
    assert IotMqttDeliveryEvent.objects.filter(event_type="RECEIVED").count() == 1


def test_exhausted_deadlock_retries_fail_closed(world, kr, payload, monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger=receipts.__name__)
    seen = _spy_attempts(monkeypatch)
    _fail_event_create(monkeypatch, [_deadlock() for _ in range(10)])
    result = receive(world, obs(kr, "A"), payload)
    assert result.status is MqttReceiptStatus.PERSISTENCE_ERROR
    assert result.error_code == "OperationalError"  # contrato público sin cambios
    assert len(seen) == receipts.DEADLOCK_MAX_ATTEMPTS
    assert IotMqttDelivery.objects.count() == 0
    assert logged_errnos(caplog)[-1] == str(receipts.MARIADB_DEADLOCK)


@pytest.mark.parametrize(
    "error",
    [
        OperationalError(receipts.MARIADB_LOCK_WAIT_TIMEOUT, "Lock wait timeout"),
        OperationalError(2013, "Lost connection"),
        OperationalError("sin errno"),
        IntegrityError(1452, "foreign key"),
    ],
)
def test_other_errors_are_not_retried(world, kr, payload, monkeypatch, error):
    seen = _spy_attempts(monkeypatch)
    _fail_event_create(monkeypatch, [error])
    result = receive(world, obs(kr, "A"), payload)
    assert result.status is MqttReceiptStatus.PERSISTENCE_ERROR
    assert len(seen) == 1
    assert IotMqttDelivery.objects.count() == 0


def test_unique_collision_is_recovered_in_a_new_transaction(world, kr, payload, monkeypatch):
    first = receive(world, obs(kr, "A"), payload).delivery
    seen = _spy_attempts(monkeypatch)
    real_find = receipts._find_existing
    calls = {"n": 0}

    def find(transport_key, *, lock):
        calls["n"] += 1
        if calls["n"] == 1:
            return None  # simula perder la carrera: el INSERT colisiona (1062)
        return real_find(transport_key, lock=lock)

    monkeypatch.setattr(receipts, "_find_existing", find)
    result = receive(world, obs(kr, "A"), payload)
    assert result.status is MqttReceiptStatus.REDELIVERY
    assert result.delivery.pk == first.pk
    assert seen == [False, False]
    assert IotMqttDelivery.objects.count() == 1
    assert IotMqttDeliveryEvent.objects.filter(event_type="REDELIVERY").count() == 1


def test_collision_without_existing_row_fails_closed(world, kr, payload, monkeypatch):
    receive(world, obs(kr, "A"), payload)
    seen = _spy_attempts(monkeypatch)
    monkeypatch.setattr(receipts, "_find_existing", lambda *_a, **_k: None)
    result = receive(world, obs(kr, "A"), payload)
    assert result.status is MqttReceiptStatus.PERSISTENCE_ERROR
    assert len(seen) == 2  # una recuperación, sin bucle
    assert IotMqttDelivery.objects.count() == 1


def test_call_inside_caller_transaction_is_rejected_without_writes(world, kr, payload, monkeypatch):
    # BUILD-FLEPSI-02M (M-02): sin ruta en línea; rechazo explícito previo a todo.
    seen = _spy_attempts(monkeypatch)
    rows_before = IotMqttDelivery.objects.count()
    events_before = IotMqttDeliveryEvent.objects.count()
    with transaction.atomic():
        with pytest.raises(TransactionManagementError, match="transacción externa"):
            receive(world, obs(kr, "A"), payload)
        with pytest.raises(TransactionManagementError):
            receive(world, None, payload)
        # La transacción del llamador sigue utilizable (no se ejecutó SQL).
        assert IotMqttDelivery.objects.count() == rows_before
    assert seen == []  # ningún intento de registro
    assert IotMqttDelivery.objects.count() == rows_before
    assert IotMqttDeliveryEvent.objects.count() == events_before
    result = receive(world, obs(kr, "A"), payload)  # fuera: funciona
    assert result.status is MqttReceiptStatus.PERSISTED
    assert seen == [False]
    again = receive(world, None, payload)
    assert again.status is MqttReceiptStatus.REDELIVERY


def test_rejected_call_inside_transaction_rolls_back_nothing_of_the_caller(world, kr, payload):
    marker = raw_payload(frame_for(world, seq=3001))
    first = receive(world, obs(kr, "A"), marker).delivery
    with pytest.raises(TransactionManagementError), transaction.atomic():
        receive(world, obs(kr, "B"), marker)
    assert IotMqttDeliveryEvent.objects.filter(delivery_id=first.pk).count() == 1
    assert IotMqttDelivery.objects.count() == 1


def test_mariadb_errno_reads_numeric_code_only():
    assert receipts.mariadb_errno(_deadlock()) == 1213
    wrapped = OperationalError(1205, "x")
    try:
        raise OperationalError(*wrapped.args) from wrapped
    except OperationalError as exc:
        assert receipts.mariadb_errno(exc) == 1205
    assert receipts.mariadb_errno(OperationalError("1213 en el texto")) is None
