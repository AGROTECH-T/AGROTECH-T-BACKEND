# ruff: noqa: S101, S106, F811
"""BUILD-FLEPSI-02: concurrencia real (hilos, conexiones independientes, barreras).

Cada operación usa su propia conexión (Django abre una por hilo) y se
sincroniza con ``Barrier``. Ninguna ejecución debe producir el error 1213
(deadlock) de MariaDB.
"""

from __future__ import annotations

import base64
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import connections

import apps.iot.services.mqtt_delivery_processing as processing_module
from apps.iot.broker_identity_config import build_keyring
from apps.iot.models import IotMqttDelivery, IotMqttDeliveryEvent
from apps.iot.models import IotMqttDeliveryState as State
from apps.iot.mqtt.broker_identity import BrokerIdentityObservation
from apps.iot.mqtt.payload import MAX_MESSAGE_BYTES
from apps.iot.services.broker_identity import register_identity, register_next_fingerprint
from apps.iot.services.mqtt_delivery_receipts import MqttReceiptStatus
from apps.iot.services.mqtt_delivery_worker import run_mqtt_delivery_worker_once
from apps.iot.services.mqtt_payload_crypto import register_encrypted_mqtt_delivery
from apps.iot.tests.mqtt_delivery_support import (  # noqa: F401 (fixtures)
    frame_for,
    mqtt_key_settings,
    raw_payload,
    world,
)

pytestmark = pytest.mark.django_db(transaction=True)


def _b64():
    return base64.b64encode(secrets.token_bytes(32)).decode("ascii")


@pytest.fixture
def kr():
    return build_keyring(raw_key=_b64(), raw_version="1", raw_previous=None, raw_next=None)


def _receive(world, keyring, value, payload):
    return register_encrypted_mqtt_delivery(
        topic=world.topic,
        payload=payload,
        qos=1,
        max_payload_bytes=MAX_MESSAGE_BYTES,
        coordinator_id=world.coordinator.pk,
        broker_identity=BrokerIdentityObservation.from_user_properties(
            [("token_id", value)], keyring
        ),
    )


def _parallel(operations):
    barrier = threading.Barrier(len(operations))

    def runner(operation):
        try:
            barrier.wait(timeout=10)
            return operation()
        except Exception as exc:  # noqa: BLE001 - se informa en la aserción
            return exc
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=len(operations)) as pool:
        return [f.result(timeout=60) for f in [pool.submit(runner, op) for op in operations]]


def _no_errors(results):
    errors = [r for r in results if isinstance(r, Exception)]
    assert not any("1213" in str(e) for e in errors), errors
    assert not errors, errors


def _bi(delivery_id):
    return list(
        IotMqttDeliveryEvent.objects.filter(
            delivery_id=delivery_id, dedupe_key__startswith="bi:"
        ).values_list("dedupe_key", flat=True)
    )


def test_two_consumers_receive_the_same_message(world, kr):
    """Caso 15: una fila PERSISTED, la otra REDELIVERY, sin eventos de identidad."""
    payload = raw_payload(frame_for(world, seq=120))
    results = _parallel([lambda: _receive(world, kr, "A", payload)] * 2)
    _no_errors(results)
    statuses = sorted(r.status.value for r in results)
    assert statuses == [MqttReceiptStatus.PERSISTED.value, MqttReceiptStatus.REDELIVERY.value]
    assert IotMqttDelivery.objects.count() == 1
    assert _bi(IotMqttDelivery.objects.get().pk) == []


def test_concurrent_redeliveries_with_different_and_equal_evidence(world, kr):
    """Caso 16: evidencias distintas -> un evento cada una; iguales -> uno solo."""
    payload = raw_payload(frame_for(world, seq=121))
    first = _receive(world, kr, "A", payload).delivery
    results = _parallel(
        [
            lambda: _receive(world, kr, "B", payload),
            lambda: _receive(world, kr, "B", payload),
            lambda: _receive(world, kr, "C", payload),
            lambda: _receive(world, kr, "A", payload),
        ]
    )
    _no_errors(results)
    assert all(r.status is MqttReceiptStatus.REDELIVERY for r in results)
    events = _bi(first.pk)
    assert len(events) == 2 and len(set(events)) == 2
    assert IotMqttDelivery.objects.get(pk=first.pk).broker_identity_conflict is True


def test_limit_holds_under_concurrency(world, kr):
    """Casos 13-14 bajo concurrencia: 5 anomalías + 1 limit + overflow exacto."""
    payload = raw_payload(frame_for(world, seq=122))
    first = _receive(world, kr, "A", payload).delivery
    results = _parallel([lambda v=v: _receive(world, kr, f"X{v}", payload) for v in range(8)])
    _no_errors(results)
    events = _bi(first.pk)
    assert sum(e.startswith("bi:mismatch:") for e in events) == 5
    assert events.count("bi:limit") == 1
    assert IotMqttDelivery.objects.get(pk=first.pk).broker_identity_overflow == 3


def test_worker_and_consumer_compete_for_the_delivery(world, kr, monkeypatch):
    """Caso 17: la reentrega espera al COMMIT del worker y queda como postfinal."""
    payload = raw_payload(frame_for(world, seq=123))
    first = _receive(world, kr, "A", payload).delivery
    real_ingest = processing_module.ingest
    started = threading.Event()

    def slow_ingest(**kwargs):
        started.set()
        time.sleep(1.5)  # mantiene el bloqueo de la fila
        return real_ingest(**kwargs)

    monkeypatch.setattr(processing_module, "ingest", slow_ingest)

    def consumer():
        assert started.wait(10)
        return _receive(world, kr, "B", payload)

    results = _parallel([lambda: run_mqtt_delivery_worker_once(worker_id="w"), consumer])
    _no_errors(results)
    assert results[1].status is MqttReceiptStatus.REDELIVERY
    final = IotMqttDelivery.objects.get(pk=first.pk)
    assert final.state == State.ACCEPTED
    assert final.broker_identity_conflict is False
    assert [e.split(":")[1] for e in _bi(first.pk)] == ["postfinal"]


def test_rotation_registration_concurrent_with_redeliveries(world):
    """Caso 25: registrar NEXT mientras llegan reentregas; sin deadlock."""
    keys = {1: _b64(), 2: _b64()}
    kr12 = build_keyring(
        raw_key=keys[1], raw_version="1", raw_previous=None, raw_next=f"2:{keys[2]}"
    )
    kr1 = build_keyring(raw_key=keys[1], raw_version="1", raw_previous=None, raw_next=None)
    register_identity(gateway_uid=world.gateway.uid, token_id="A", keyring=kr1, process_id="t")
    payload = raw_payload(frame_for(world, seq=124))
    first = _receive(world, kr1, "A", payload).delivery
    results = _parallel(
        [
            lambda: register_next_fingerprint(
                gateway_uid=world.gateway.uid, token_id="A", keyring=kr12, process_id="t"
            ),
            lambda: _receive(world, kr12, "A", payload),
            lambda: _receive(world, kr12, "B", payload),
        ]
    )
    _no_errors(results)
    assert results[0].key_version == 2
    assert [e.split(":")[2] for e in _bi(first.pk)] == ["v1"]
