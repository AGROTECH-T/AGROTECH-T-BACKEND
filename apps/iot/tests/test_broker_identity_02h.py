# ruff: noqa: S101, S105, S106, F811
"""BUILD-FLEPSI-02H: aislamiento de errores, overflow exacto, colisiones UNIQUE
y entrada segura del token, contra MariaDB 11.8 desechable."""

from __future__ import annotations

import base64
import io
import json
import secrets
import threading
from types import SimpleNamespace

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, OperationalError, connections

import apps.iot.services.broker_identity as identity_module
from apps.iot.broker_identity_config import (
    BrokerIdentityConfig,
    BrokerIdentityMode,
    build_keyring,
)
from apps.iot.management.commands import mqtt_broker_identity as identity_command
from apps.iot.models import IotMqttDelivery, IotMqttDeliveryEvent
from apps.iot.models import IotMqttDeliveryState as State
from apps.iot.mqtt import consumer as consumer_module
from apps.iot.mqtt.broker_identity import BrokerIdentityObservation
from apps.iot.mqtt.consumer import MqttConsumer, MqttSettings
from apps.iot.mqtt.payload import MAX_MESSAGE_BYTES
from apps.iot.services.mqtt_delivery_receipts import MqttReceiptStatus
from apps.iot.services.mqtt_delivery_worker import run_mqtt_delivery_worker_once
from apps.iot.services.mqtt_payload_crypto import register_encrypted_mqtt_delivery
from apps.iot.tests.mqtt_delivery_support import (  # noqa: F401 (fixtures)
    frame_for,
    mqtt_key_settings,
    raw_payload,
    world,
)
from apps.iot.tests.test_broker_identity_concurrency import _no_errors, _parallel
from apps.iot.tests.test_mqtt_consumer import FakeClient, FakeMessage

pytestmark = pytest.mark.django_db(transaction=True)
SENTINEL = "SENTINEL-TOKEN-7f3a9c"


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


def events(delivery_id, event_type=None):
    qs = IotMqttDeliveryEvent.objects.filter(delivery_id=delivery_id)
    if event_type:
        qs = qs.filter(event_type=event_type)
    return list(qs.values_list("event_type", "dedupe_key", "safe_detail", "result_code"))


def bi(delivery_id):
    return [e[1] for e in events(delivery_id) if e[1] and e[1].startswith("bi:")]


def row(pk):
    return IotMqttDelivery.objects.get(pk=pk)


@pytest.fixture
def payload(world):
    return raw_payload(frame_for(world, seq=150))


# -- H-01 ---------------------------------------------------------------------


def test_new_delivery_with_failing_observation_is_durable_with_null_evidence(
    world, kr, payload, monkeypatch
):
    def boom(self):
        raise RuntimeError(f"fallo {SENTINEL}")

    monkeypatch.setattr(BrokerIdentityObservation, "evidence_for_new_delivery", boom)
    result = receive(world, obs(kr, SENTINEL), payload)

    assert result.status is MqttReceiptStatus.PERSISTED
    stored = row(result.delivery.pk)
    assert stored.broker_identity_presence is None  # no se inventa identidad
    assert stored.broker_token_fingerprint is None
    errors = events(stored.pk, "BROKER_IDENTITY_OBSERVATION_ERROR")
    assert len(errors) == 1 and errors[0][3] == "receipt:evidence:RuntimeError"
    assert SENTINEL not in json.dumps(errors)


def test_redelivery_error_after_a_write_rolls_back_only_identity(world, kr, payload, monkeypatch):
    first = receive(world, obs(kr, "A"), payload).delivery
    before = row(first.pk)

    def partial_then_fail(target, _observation):
        IotMqttDelivery.objects.filter(pk=target.pk).update(broker_identity_conflict=True)
        raise KeyError("bug tras escribir")

    monkeypatch.setattr(identity_module, "record_redelivery_identity", partial_then_fail)
    result = receive(world, obs(kr, "B"), payload)

    assert result.status is MqttReceiptStatus.REDELIVERY  # el ACK no se bloquea
    after = row(first.pk)
    assert after.broker_identity_conflict is False  # savepoint revertido
    assert (after.broker_identity_presence, bytes(after.broker_token_fingerprint)) == (
        before.broker_identity_presence,
        bytes(before.broker_token_fingerprint),
    )
    assert bi(first.pk) == []  # sin anomalía falsa
    assert len(events(first.pk, "REDELIVERY")) == 1
    diagnostic = events(first.pk, "BROKER_IDENTITY_OBSERVATION_ERROR")[0][3]
    assert diagnostic == "redelivery:record:KeyError"


def test_historic_null_row_untouched_when_observation_fails(world, kr, payload, monkeypatch):
    first = receive(world, None, payload).delivery
    before = row(first.pk)
    monkeypatch.setattr(
        identity_module,
        "record_redelivery_identity",
        lambda *_a: (_ for _ in ()).throw(ValueError("x")),
    )
    receive(world, obs(kr, "A"), payload)
    after = row(first.pk)
    assert after.broker_identity_presence is None and after.updated_at == before.updated_at


def test_database_error_on_new_delivery_still_blocks_ack(world, kr, payload, monkeypatch):
    monkeypatch.setattr(
        BrokerIdentityObservation,
        "evidence_for_new_delivery",
        lambda self: (_ for _ in ()).throw(RuntimeError("x")),
    )
    monkeypatch.setattr(
        "apps.iot.services.mqtt_delivery_receipts.record_observation_failure",
        lambda *_a, **_k: (_ for _ in ()).throw(OperationalError("db caída")),
    )
    result = receive(world, obs(kr, "A"), payload)
    assert result.status is MqttReceiptStatus.PERSISTENCE_ERROR
    assert IotMqttDelivery.objects.count() == 0


def test_database_error_on_redelivery_still_blocks_ack(world, kr, payload, monkeypatch):
    first = receive(world, obs(kr, "A"), payload).delivery
    monkeypatch.setattr(
        identity_module,
        "record_redelivery_identity",
        lambda *_a: (_ for _ in ()).throw(OperationalError("db caída")),
    )
    result = receive(world, obs(kr, "B"), payload)
    assert result.status is MqttReceiptStatus.PERSISTENCE_ERROR
    assert events(first.pk, "REDELIVERY") == []  # todo revertido
    assert events(first.pk, "BROKER_IDENTITY_OBSERVATION_ERROR") == []


def test_observation_failure_never_dead_letters(world, kr, payload, monkeypatch):
    monkeypatch.setenv("IOT_BROKER_IDENTITY_MODE", "record")
    monkeypatch.setattr(
        BrokerIdentityObservation,
        "evidence_for_new_delivery",
        lambda self: (_ for _ in ()).throw(RuntimeError("x")),
    )
    result = receive(world, obs(kr, "A"), payload)
    monkeypatch.undo()
    monkeypatch.setenv("IOT_BROKER_IDENTITY_MODE", "record")
    run_mqtt_delivery_worker_once(worker_id="w")
    final = row(result.delivery.pk)
    assert final.state == State.ACCEPTED
    # BUILD-FLEPSI-02J (J-02): una identidad no observada ya no queda exenta.
    would = [e[3] for e in events(final.pk, "BROKER_IDENTITY_WOULD_REJECT")]
    assert would == ["would_reject_not_observed"]


def test_consumer_acks_only_after_commit_even_if_classification_fails(
    world, kr, payload, monkeypatch
):
    seen = []

    class CheckingClient(FakeClient):
        def ack(self, mid, qos):
            # Otra conexión solo ve filas confirmadas (COMMIT).
            def look():
                seen.append(IotMqttDelivery.objects.count())
                connections.close_all()

            thread = threading.Thread(target=look)
            thread.start()
            thread.join()
            return super().ack(mid, qos)

    monkeypatch.setattr(
        BrokerIdentityObservation,
        "from_user_properties",
        classmethod(lambda cls, *_a: (_ for _ in ()).throw(RuntimeError(SENTINEL))),
    )
    monkeypatch.setattr(consumer_module, "_coordinator_candidate", lambda t, p: None)
    client = CheckingClient()
    consumer = MqttConsumer(
        MqttSettings(host="h", port=1, client_id="c"),
        client=client,
        broker_identity=BrokerIdentityConfig(mode=BrokerIdentityMode.RECORD, keyring=kr),
    )
    message = FakeMessage(topic=world.topic, payload=payload)
    message.properties = SimpleNamespace(UserProperty=[("token_id", SENTINEL)])
    consumer._on_message(client, None, message)

    assert client.ack_calls == [(42, 1)]
    assert seen == [1]  # la fila ya era visible (confirmada) al enviar el ACK
    delivery = IotMqttDelivery.objects.get()
    assert delivery.broker_identity_presence is None
    diagnostic = events(delivery.pk, "BROKER_IDENTITY_OBSERVATION_ERROR")[0][3]
    assert diagnostic == "receipt:classify:RuntimeError"


# -- H-02 ---------------------------------------------------------------------


def test_overflow_counts_distinct_evidences_not_retransmissions(world, kr, payload):
    first = receive(world, obs(kr, "A"), payload).delivery
    for index in range(5):
        receive(world, obs(kr, f"X{index}"), payload)
    receive(world, obs(kr, "SIXTH"), payload)
    assert row(first.pk).broker_identity_overflow == 1
    receive(world, obs(kr, "SIXTH"), payload)
    assert row(first.pk).broker_identity_overflow == 1
    receive(world, obs(kr, "SEVENTH"), payload)
    assert row(first.pk).broker_identity_overflow == 2
    for value in ("SIXTH", "SEVENTH", "SIXTH", "X0"):
        receive(world, obs(kr, value), payload)
    stored = row(first.pk)
    assert stored.broker_identity_overflow == 2
    keys = bi(first.pk)
    assert sum(k.startswith("bi:mismatch:") for k in keys) == 5
    assert keys.count("bi:limit") == 1
    assert sum(k.startswith("bi:overflow:") for k in keys) == 2


def test_overflow_saturates_at_cap(world, kr, payload, monkeypatch):
    monkeypatch.setattr(identity_module, "MAX_OVERFLOW_EVIDENCES", 2)
    first = receive(world, obs(kr, "A"), payload).delivery
    for index in range(10):
        receive(world, obs(kr, f"V{index}"), payload)
    keys = bi(first.pk)
    assert sum(k.startswith("bi:overflow:") for k in keys) == 2
    assert keys.count("bi:overflowcap") == 1
    assert row(first.pk).broker_identity_overflow == 2


def test_overflow_rollback_keeps_counter_and_events_consistent(world, kr, payload, monkeypatch):
    first = receive(world, obs(kr, "A"), payload).delivery
    for index in range(5):
        receive(world, obs(kr, f"X{index}"), payload)
    real = identity_module._create_event

    def fail_on_overflow(row_, dedupe_key, *args):
        if dedupe_key.startswith("bi:overflow:"):
            raise OperationalError("db caída")
        return real(row_, dedupe_key, *args)

    monkeypatch.setattr(identity_module, "_create_event", fail_on_overflow)
    result = receive(world, obs(kr, "SIXTH"), payload)
    assert result.status is MqttReceiptStatus.PERSISTENCE_ERROR
    assert row(first.pk).broker_identity_overflow == 0
    assert not any(k.startswith("bi:overflow:") for k in bi(first.pk))


def test_concurrent_overflow_distinct_and_identical(world, kr, payload):
    first = receive(world, obs(kr, "A"), payload).delivery
    for index in range(5):
        receive(world, obs(kr, f"X{index}"), payload)
    results = _parallel(
        [lambda v=v: receive(world, obs(kr, v), payload) for v in ("P", "P", "Q", "R", "Q")]
    )
    _no_errors(results)
    assert all(r.status is MqttReceiptStatus.REDELIVERY for r in results)
    assert row(first.pk).broker_identity_overflow == 3  # P, Q, R una vez cada una
    assert sum(k.startswith("bi:overflow:") for k in bi(first.pk)) == 3


# -- H-03 ---------------------------------------------------------------------


def _skip_first_exists(monkeypatch):
    real = identity_module._event_exists
    state = {"skipped": False}

    def exists(row_, key):
        if not state["skipped"]:
            state["skipped"] = True
            return False  # fuerza el INSERT aunque el evento ya exista
        return real(row_, key)

    monkeypatch.setattr(identity_module, "_event_exists", exists)
    # BUILD-FLEPSI-02J: la comprobación por huella entre categorías (J-03)
    # también detectaría el evento; se omite en la primera llamada para que la
    # prueba siga recorriendo la ruta real INSERT -> colisión (H-03).
    real_categories = identity_module._categories_for_evidence
    categories_state = {"skipped": False}

    def categories(row_, evidence_id):
        if not categories_state["skipped"]:
            categories_state["skipped"] = True
            return set()
        return real_categories(row_, evidence_id)

    monkeypatch.setattr(identity_module, "_categories_for_evidence", categories)


def test_real_dedupe_collision_is_absorbed_without_duplicates(world, kr, payload, monkeypatch):
    first = receive(world, obs(kr, "A"), payload).delivery
    receive(world, obs(kr, "B"), payload)  # crea bi:mismatch
    _skip_first_exists(monkeypatch)
    result = receive(world, obs(kr, "B"), payload)  # INSERT real -> 1062 real
    assert result.status is MqttReceiptStatus.REDELIVERY
    assert len(bi(first.pk)) == 1


@pytest.mark.parametrize(
    ("errno", "message", "event_exists", "absorbed"),
    [
        (1062, "Entrada duplicada 'x' para la clave 'iot_mqtt_event_dedupe_uniq'", True, True),
        (1062, "mensaje sin nombre de índice", True, True),
        (1062, "Duplicate entry 'x' for key 'iot_mqtt_event_dedupe_uniq'", False, False),
        (1062, "Duplicate entry 'x' for key 'otro_indice'", False, False),
        (1452, "Cannot add or update a child row", True, False),
    ],
)
def test_collision_detection_is_structural(
    world, kr, payload, monkeypatch, errno, message, event_exists, absorbed
):
    first = receive(world, obs(kr, "A"), payload).delivery
    if event_exists:
        receive(world, obs(kr, "B"), payload)
    _skip_first_exists(monkeypatch)
    monkeypatch.setattr(
        identity_module,
        "_create_event",
        lambda *_a: (_ for _ in ()).throw(IntegrityError(errno, message)),
    )
    result = receive(world, obs(kr, "B"), payload)
    expected = MqttReceiptStatus.REDELIVERY if absorbed else MqttReceiptStatus.PERSISTENCE_ERROR
    assert result.status is expected
    assert len(bi(first.pk)) == (1 if event_exists else 0)


# -- H-05 ---------------------------------------------------------------------


def _env_ring(monkeypatch):
    key = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
    monkeypatch.delenv("IOT_BROKER_IDENTITY_MODE", raising=False)
    monkeypatch.setenv("IOT_BROKER_IDENTITY_HMAC_KEY", key)
    monkeypatch.setenv("IOT_BROKER_IDENTITY_HMAC_KEY_VERSION", "1")


class TtyStream(io.StringIO):
    def isatty(self):
        return True


def test_interactive_registration_hides_token(world, monkeypatch):
    _env_ring(monkeypatch)
    monkeypatch.setattr(identity_command.getpass, "getpass", lambda _p: SENTINEL)
    out, err = io.StringIO(), io.StringIO()
    call_command(
        "mqtt_broker_identity",
        "register",
        "--gateway-uid",
        world.gateway.uid,
        "--operator",
        "op",
        "--token-stdin",
        stdin=TtyStream(),
        stdout=out,
        stderr=err,
    )
    assert json.loads(out.getvalue())["versions"] == [1]
    assert SENTINEL not in out.getvalue() + err.getvalue()


def test_piped_registration_and_invalid_token_do_not_leak(world, monkeypatch):
    _env_ring(monkeypatch)
    with pytest.raises(CommandError) as info:
        call_command(
            "mqtt_broker_identity",
            "register",
            "--gateway-uid",
            world.gateway.uid,
            "--operator",
            "op",
            "--token-stdin",
            stdin=io.StringIO(f" {SENTINEL}\n"),
            stdout=io.StringIO(),
        )
    assert SENTINEL not in str(info.value)
    out = io.StringIO()
    call_command(
        "mqtt_broker_identity",
        "register",
        "--gateway-uid",
        world.gateway.uid,
        "--operator",
        "op",
        "--token-stdin",
        stdin=io.StringIO(SENTINEL + "\n"),
        stdout=out,
    )
    assert SENTINEL not in out.getvalue()
