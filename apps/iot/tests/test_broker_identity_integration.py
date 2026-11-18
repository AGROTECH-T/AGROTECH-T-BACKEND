# ruff: noqa: S101, S105, S106, S608, F811
"""BUILD-FLEPSI-02: identidad del broker contra MariaDB 11.8 desechable."""

from __future__ import annotations

import base64
import io
import json
import secrets

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError, OperationalError, connection

import apps.iot.services.broker_identity as identity_module
import apps.iot.services.mqtt_delivery_processing as processing_module
from apps.iot.broker_identity_config import build_keyring
from apps.iot.models import (
    BrokerIdentity,
    BrokerIdentityFingerprint,
    IotMqttDelivery,
    IotMqttDeliveryEvent,
    Measurement,
)
from apps.iot.models import IotMqttDeliveryState as State
from apps.iot.mqtt.broker_identity import BrokerIdentityObservation, token_fingerprint
from apps.iot.mqtt.payload import MAX_MESSAGE_BYTES
from apps.iot.services.broker_identity import (
    BrokerIdentityError,
    register_identity,
    register_next_fingerprint,
    revoke_identity,
)
from apps.iot.services.mqtt_delivery_receipts import MqttReceiptStatus
from apps.iot.services.mqtt_delivery_worker import run_mqtt_delivery_worker_once
from apps.iot.services.mqtt_payload_crypto import (
    decrypt_mqtt_delivery,
    register_encrypted_mqtt_delivery,
)
from apps.iot.tests.mqtt_delivery_support import (  # noqa: F401 (fixtures)
    frame_for,
    lanes,
    mqtt_key_settings,
    raw_payload,
    world,
)

pytestmark = pytest.mark.django_db(transaction=True)
SENTINEL = "SENTINEL-TOKEN-7f3a9c"


def _b64() -> str:
    return base64.b64encode(secrets.token_bytes(32)).decode("ascii")


@pytest.fixture
def keys():
    return {1: _b64(), 2: _b64(), 3: _b64()}


def ring(keys, current, previous=(), next_=None):
    return build_keyring(
        raw_key=keys[current],
        raw_version=str(current),
        raw_previous=",".join(f"{v}:{keys[v]}" for v in previous) or None,
        raw_next=f"{next_}:{keys[next_]}" if next_ else None,
    )


def obs(keyring, *props):
    return BrokerIdentityObservation.from_user_properties(list(props), keyring)


def tok(value):
    return ("token_id", value)


def receive(world, keyring_or_none, *props, seq=120, payload=None):
    payload = payload or raw_payload(frame_for(world, seq=seq))
    identity = None if keyring_or_none is None else obs(keyring_or_none, *props)
    return register_encrypted_mqtt_delivery(
        topic=world.topic,
        payload=payload,
        qos=1,
        max_payload_bytes=MAX_MESSAGE_BYTES,
        coordinator_id=world.coordinator.pk,
        broker_identity=identity,
    ), payload


def bi_events(delivery):
    return list(
        IotMqttDeliveryEvent.objects.filter(delivery=delivery, dedupe_key__startswith="bi:")
        .order_by("occurred_at", "id")
        .values_list("dedupe_key", flat=True)
    )


def row(delivery):
    return IotMqttDelivery.objects.get(pk=delivery.pk)


# -- Casos 1-5: primera recepción ---------------------------------------------------


@pytest.mark.parametrize(
    ("props", "presence", "has_fp"),
    [
        ((tok("12345"),), "single", True),
        ((), "absent", False),
        ((tok("1"), tok("2")), "multiple", False),
        ((tok(" 1"),), "invalid", False),
        ((tok("x" * 300),), "oversized", False),
    ],
)
def test_first_receipt_stores_classified_evidence(world, keys, props, presence, has_fp):
    kr = ring(keys, 1)
    result, _ = receive(world, kr, *props)
    stored = row(result.delivery)
    assert result.status is MqttReceiptStatus.PERSISTED
    assert stored.broker_identity_presence == presence
    assert stored.broker_token_key_version == 1
    assert (stored.broker_token_fingerprint is not None) is has_fp
    if has_fp:
        assert bytes(stored.broker_token_fingerprint) == token_fingerprint(kr.key_for(1), b"12345")
    assert bi_events(stored) == []


# -- Casos 6-9: reentregas ---------------------------------------------------------


def test_redelivery_with_same_identity_records_nothing(world, keys):
    kr = ring(keys, 1)
    first, payload = receive(world, kr, tok("A"))
    second, _ = receive(world, kr, tok("A"), payload=payload)
    assert second.status is MqttReceiptStatus.REDELIVERY
    assert bi_events(first.delivery) == []
    assert row(first.delivery).broker_identity_conflict is False


def test_redelivery_with_other_identity_marks_conflict_once(world, keys):
    kr = ring(keys, 1)
    first, payload = receive(world, kr, tok("A"))
    for _ in range(3):
        result, _ = receive(world, kr, tok("B"), payload=payload)
        assert result.status is MqttReceiptStatus.REDELIVERY  # ACK nunca se bloquea
    events = bi_events(first.delivery)
    assert len(events) == 1 and events[0].startswith("bi:mismatch:v1:")
    assert row(first.delivery).broker_identity_conflict is True


def test_redelivery_after_rotation_compares_in_stored_version(world, keys):
    first, payload = receive(world, ring(keys, 1), tok("A"))
    rotated = ring(keys, 2, previous=(1,))
    same, _ = receive(world, rotated, tok("A"), payload=payload)
    assert same.status is MqttReceiptStatus.REDELIVERY
    assert bi_events(first.delivery) == []  # sin falso mismatch
    receive(world, rotated, tok("B"), payload=payload)
    assert [e.split(":")[2] for e in bi_events(first.delivery)] == ["v1"]


def test_retired_key_gives_single_unverifiable_without_conflict(world, keys):
    first, payload = receive(world, ring(keys, 1), tok("A"))
    retired = ring(keys, 2)  # v1 ya no está en el anillo
    for value in ("A", "B", "A"):
        result, _ = receive(world, retired, tok(value), payload=payload)
        assert result.status is MqttReceiptStatus.REDELIVERY
    assert bi_events(first.delivery) == ["bi:unverifiable:v1"]
    assert row(first.delivery).broker_identity_conflict is False


# -- Casos 10-11: históricas NULL --------------------------------------------------


def test_historic_null_delivery_is_never_modified_and_gets_one_late(world, keys):
    first, payload = receive(world, None)  # modo off / histórica
    before = row(first.delivery)
    for value in ("A", "B", "C"):
        receive(world, ring(keys, 1), tok(value), payload=payload)
    receive(world, ring(keys, 2), tok("D"), payload=payload)
    after = row(first.delivery)
    assert bi_events(first.delivery) == ["bi:late"]
    assert after.broker_identity_presence is None
    assert after.broker_token_fingerprint is None and after.broker_token_key_version is None
    assert after.broker_identity_conflict is False and after.broker_identity_overflow == 0
    assert after.updated_at == before.updated_at


# -- Casos 12-14: límite ----------------------------------------------------------


def test_five_anomalies_then_single_limit_and_overflow(world, keys):
    kr = ring(keys, 1)
    first, payload = receive(world, kr, tok("A"))
    for index in range(8):
        receive(world, kr, tok(f"X{index}"), payload=payload)
    events = bi_events(first.delivery)
    assert sum(e.startswith("bi:mismatch:") for e in events) == 5
    assert events.count("bi:limit") == 1
    assert row(first.delivery).broker_identity_overflow == 3


# -- Caso 18: fallo antes del commit ---------------------------------------------


def test_database_failure_rolls_back_flag_and_event(world, keys, monkeypatch):
    kr = ring(keys, 1)
    first, payload = receive(world, kr, tok("A"))

    def failing(*_args, **_kwargs):
        raise OperationalError("db caída")

    monkeypatch.setattr(identity_module, "_create_event", failing)
    result, _ = receive(world, kr, tok("B"), payload=payload)
    assert result.status is MqttReceiptStatus.PERSISTENCE_ERROR  # sin ACK
    assert row(first.delivery).broker_identity_conflict is False
    assert bi_events(first.delivery) == []
    monkeypatch.undo()
    retry, _ = receive(world, kr, tok("B"), payload=payload)  # reentrega QoS 1
    assert retry.status is MqttReceiptStatus.REDELIVERY
    assert len(bi_events(first.delivery)) == 1


# -- Caso 20: entrega final --------------------------------------------------------


def test_final_delivery_keeps_state_and_measurements(world, keys):
    kr = ring(keys, 1)
    first, payload = receive(world, kr, tok("A"))
    assert run_mqtt_delivery_worker_once(worker_id="w").processed == 1
    measurements = Measurement.objects.count()
    receive(world, kr, tok("B"), payload=payload)
    final = row(first.delivery)
    assert final.state == State.ACCEPTED
    assert final.broker_identity_conflict is False
    assert Measurement.objects.count() == measurements
    assert bi_events(final)[0].startswith("bi:postfinal:v1:")


# -- Casos 21-22, 24: modos y veredicto --------------------------------------------


def _would_reject(delivery):
    return list(
        IotMqttDeliveryEvent.objects.filter(
            delivery=delivery, event_type="BROKER_IDENTITY_WOULD_REJECT"
        ).values_list("result_code", flat=True)
    )


@pytest.mark.parametrize(
    ("props", "register", "expected"),
    [
        ((tok("A"),), "A", []),
        ((tok("A"),), None, ["would_reject_unregistered"]),
        ((tok("A"),), "Z", ["would_reject_mismatch"]),
        ((), "A", ["would_reject_missing"]),
        ((tok("1"), tok("2")), "A", ["would_reject_ambiguous"]),
        ((tok(" bad"),), "A", ["would_reject_invalid"]),
    ],
)
def test_record_mode_verdict_never_rejects(world, keys, monkeypatch, props, register, expected):
    monkeypatch.setenv("IOT_BROKER_IDENTITY_MODE", "record")
    kr = ring(keys, 1)
    if register:
        register_identity(
            gateway_uid=world.gateway.uid, token_id=register, keyring=kr, process_id="pytest"
        )
    result, _ = receive(world, kr, *props)
    assert run_mqtt_delivery_worker_once(worker_id="w").processed == 1
    final = row(result.delivery)
    assert final.state == State.ACCEPTED  # record nunca rechaza
    assert _would_reject(final) == expected


def test_conflicted_delivery_is_ambiguous_in_record(world, keys, monkeypatch):
    monkeypatch.setenv("IOT_BROKER_IDENTITY_MODE", "record")
    kr = ring(keys, 1)
    register_identity(gateway_uid=world.gateway.uid, token_id="A", keyring=kr, process_id="t")
    first, payload = receive(world, kr, tok("A"))
    receive(world, kr, tok("B"), payload=payload)
    run_mqtt_delivery_worker_once(worker_id="w")
    assert _would_reject(row(first.delivery)) == ["would_reject_ambiguous"]
    assert row(first.delivery).state == State.ACCEPTED


def test_mode_off_and_historic_rows_are_exempt(world, keys, monkeypatch):
    monkeypatch.setenv("IOT_BROKER_IDENTITY_MODE", "record")
    result, _ = receive(world, None)
    run_mqtt_delivery_worker_once(worker_id="w")
    assert _would_reject(row(result.delivery)) == []
    assert row(result.delivery).broker_identity_presence is None


def test_pending_delivery_verifiable_after_key_retirement(world, keys, monkeypatch):
    monkeypatch.setenv("IOT_BROKER_IDENTITY_MODE", "record")
    kr1 = ring(keys, 1)
    register_identity(gateway_uid=world.gateway.uid, token_id="A", keyring=kr1, process_id="t")
    result, _ = receive(world, kr1, tok("A"))
    # El worker no usa claves: compara huellas guardadas en v1.
    run_mqtt_delivery_worker_once(worker_id="w")
    assert _would_reject(row(result.delivery)) == []


def test_verdict_bug_never_dead_letters(world, keys, monkeypatch):
    monkeypatch.setenv("IOT_BROKER_IDENTITY_MODE", "record")
    result, _ = receive(world, ring(keys, 1), tok("A"))

    def broken(_delivery):
        raise KeyError("bug")

    monkeypatch.setattr(identity_module, "identity_verdict", broken)
    run_mqtt_delivery_worker_once(worker_id="w")
    assert row(result.delivery).state == State.ACCEPTED


def test_unmapped_ingestion_status_is_impossible_for_identity(world):
    """Record no añade IngestionStatus: el mapeo existente cubre todos."""
    from apps.iot.services.ingestion import IngestionResult, IngestionStatus

    for status in IngestionStatus:
        processing_module._state_for_ingestion_result(IngestionResult(status))


# -- Registro y rotación ---------------------------------------------------------


def test_registry_rotation_and_uniqueness(world, keys, lanes, admin, farm_id):
    import apps.iot.services as iot

    kr = ring(keys, 1, next_=2)
    identity = register_identity(
        gateway_uid=world.gateway.uid, token_id="A", keyring=kr, process_id="t"
    )
    assert sorted(identity.fingerprints.values_list("key_version", flat=True)) == [1, 2]
    other = iot.create_gateway(user=admin, farm_id=farm_id, name="Otro")
    with pytest.raises(BrokerIdentityError, match="otra identidad"):
        register_identity(gateway_uid=other.uid, token_id="A", keyring=kr, process_id="t")
    with pytest.raises(BrokerIdentityError, match="no coincide"):
        register_next_fingerprint(
            gateway_uid=world.gateway.uid, token_id="B", keyring=kr, process_id="t"
        )
    assert (
        register_next_fingerprint(
            gateway_uid=world.gateway.uid, token_id="A", keyring=kr, process_id="t"
        ).key_version
        == 2
    )
    revoked = revoke_identity(gateway_uid=world.gateway.uid, cause="rotation", process_id="t")
    assert revoked.active_slot is None and revoked.revocation_cause == "rotation"
    assert BrokerIdentityFingerprint.objects.filter(identity=revoked).count() == 2


def _env_ring(monkeypatch, keys, current, previous=(), next_=None):
    monkeypatch.setenv("IOT_BROKER_IDENTITY_HMAC_KEY", keys[current])
    monkeypatch.setenv("IOT_BROKER_IDENTITY_HMAC_KEY_VERSION", str(current))
    if previous:
        monkeypatch.setenv(
            "IOT_BROKER_IDENTITY_HMAC_KEY_PREVIOUS", ",".join(f"{v}:{keys[v]}" for v in previous)
        )
    else:
        monkeypatch.delenv("IOT_BROKER_IDENTITY_HMAC_KEY_PREVIOUS", raising=False)
    if next_:
        monkeypatch.setenv("IOT_BROKER_IDENTITY_HMAC_KEY_NEXT", f"{next_}:{keys[next_]}")
    else:
        monkeypatch.delenv("IOT_BROKER_IDENTITY_HMAC_KEY_NEXT", raising=False)


def _cmd(*args, stdin=None):
    out = io.StringIO()
    call_command("mqtt_broker_identity", *args, stdout=out, stdin=stdin)
    return json.loads(out.getvalue()) if out.getvalue().strip() else None


def test_rotation_commands_gate_activation_and_retirement(world, keys, monkeypatch):
    monkeypatch.delenv("IOT_BROKER_IDENTITY_MODE", raising=False)
    _env_ring(monkeypatch, keys, 1)
    _cmd(
        "register",
        "--gateway-uid",
        world.gateway.uid,
        "--operator",
        "op",
        "--token-stdin",
        stdin=io.StringIO(SENTINEL + "\n"),
    )
    _env_ring(monkeypatch, keys, 1, next_=2)
    with pytest.raises(CommandError, match="sin huella NEXT"):
        _cmd("activation-check")
    assert _cmd("missing")["gateways"] == [world.gateway.uid]
    _cmd(
        "register-next",
        "--gateway-uid",
        world.gateway.uid,
        "--operator",
        "op",
        "--token-stdin",
        stdin=io.StringIO(SENTINEL + "\n"),
    )
    assert _cmd("activation-check")["missing"] == 0
    # Activación = cambio de configuración: v2 vigente, v1 previous.
    _env_ring(monkeypatch, keys, 2, previous=(1,))
    kr = ring(keys, 1)
    receive(world, kr, tok(SENTINEL))  # entrega pendiente en v1
    with pytest.raises(CommandError, match="criterios"):
        _cmd("retirement-check", "--version", "1")
    assert _cmd("versions")["nonfinal_deliveries_by_version"] == {"v1": 1}
    run_mqtt_delivery_worker_once(worker_id="w")
    report = _cmd("retirement-check", "--version", "1")
    assert report["objective_criteria_met"] is True


def test_token_is_never_stored_in_clear(world, keys, monkeypatch):
    monkeypatch.setenv("IOT_BROKER_IDENTITY_MODE", "record")
    kr = ring(keys, 1)
    register_identity(gateway_uid=world.gateway.uid, token_id=SENTINEL, keyring=kr, process_id="t")
    first, payload = receive(world, kr, tok(SENTINEL))
    receive(world, kr, tok(SENTINEL + "-otro"), payload=payload)
    run_mqtt_delivery_worker_once(worker_id="w")
    needle_text = SENTINEL
    needle_bytes = SENTINEL.encode()
    tables = (
        "iot_mqtt_deliveries",
        "iot_mqtt_delivery_events",
        "iot_broker_identities",
        "iot_broker_identity_fingerprints",
    )
    with connection.cursor() as cursor:
        for table in tables:
            cursor.execute(f"SELECT * FROM {connection.ops.quote_name(table)}")  # noqa: S608
            for record in cursor.fetchall():
                for value in record:
                    if isinstance(value, (bytes, bytearray, memoryview)):
                        assert needle_bytes not in bytes(value)
                    else:
                        assert needle_text not in str(value)
    assert decrypt_mqtt_delivery(row(first.delivery)) == payload  # AAD intacto


# -- Casos 26-28: triggers --------------------------------------------------------


def test_triggers_block_update_delete_and_cascade(world, keys):
    kr = ring(keys, 1)
    first, _ = receive(world, kr, tok("A"))
    event_id = IotMqttDeliveryEvent.objects.filter(delivery=first.delivery).first().pk
    identity = register_identity(
        gateway_uid=world.gateway.uid, token_id="A", keyring=kr, process_id="t"
    )
    fp_id = identity.fingerprints.first().pk
    quote = connection.ops.quote_name
    statements = [
        (
            f"UPDATE {quote('iot_mqtt_delivery_events')} SET safe_detail='x' WHERE id=%s",
            [event_id.hex],
        ),
        (f"DELETE FROM {quote('iot_mqtt_delivery_events')} WHERE id=%s", [event_id.hex]),
        (
            f"UPDATE {quote('iot_broker_identity_fingerprints')} SET created_process_id='x' "
            "WHERE id=%s",
            [fp_id],
        ),
        (f"DELETE FROM {quote('iot_broker_identity_fingerprints')} WHERE id=%s", [fp_id]),
    ]
    for sql, params in statements:
        with pytest.raises(DatabaseError), connection.cursor() as cursor:
            cursor.execute(sql, params)
    with pytest.raises(DatabaseError):
        IotMqttDelivery.objects.filter(pk=first.delivery.pk).delete()  # cascada ORM
    assert IotMqttDeliveryEvent.objects.filter(pk=event_id).exists()
    assert BrokerIdentity.objects.filter(pk=identity.pk).exists()
