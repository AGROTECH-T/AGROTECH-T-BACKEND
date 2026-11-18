# ruff: noqa: S101, S105, S106, F811
"""BUILD-FLEPSI-02J: estados de evidencia (J-01), veredicto explícito (J-02),
conteo por huella entre categorías (J-03) y diagnósticos deduplicados (J-06).

Contra MariaDB 11.8 desechable. Sin cambios de esquema.
"""

from __future__ import annotations

import base64
import secrets

import pytest
from django.db import OperationalError

import apps.iot.services.broker_identity as identity_module
from apps.iot.broker_identity_config import build_keyring
from apps.iot.models import IotMqttDelivery, IotMqttDeliveryEvent
from apps.iot.models import IotMqttDeliveryState as State
from apps.iot.mqtt.broker_identity import (
    BrokerIdentityObservation,
    BrokerIdentityObservationFailure,
)
from apps.iot.mqtt.payload import MAX_MESSAGE_BYTES
from apps.iot.services.broker_identity import (
    IdentityVerdictStatus as V,
)
from apps.iot.services.broker_identity import (
    identity_verdict,
    observation_failed_at_receipt,
    register_identity,
)
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

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def kr():
    key = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
    return build_keyring(raw_key=key, raw_version="1", raw_previous=None, raw_next=None)


@pytest.fixture
def payload(world):
    return raw_payload(frame_for(world, seq=160))


def obs(keyring, *props):
    return BrokerIdentityObservation.from_user_properties(list(props), keyring)


def tok(value):
    return ("token_id", value)


def receive(world, identity, payload):
    return register_encrypted_mqtt_delivery(
        topic=world.topic,
        payload=payload,
        qos=1,
        max_payload_bytes=MAX_MESSAGE_BYTES,
        coordinator_id=world.coordinator.pk,
        broker_identity=identity,
    )


def row(pk):
    return IotMqttDelivery.objects.get(pk=pk)


def keys(pk, prefix="bi:"):
    return list(
        IotMqttDeliveryEvent.objects.filter(delivery_id=pk, dedupe_key__startswith=prefix)
        .order_by("occurred_at", "id")
        .values_list("dedupe_key", flat=True)
    )


def diags(pk):
    return list(
        IotMqttDeliveryEvent.objects.filter(
            delivery_id=pk, event_type="BROKER_IDENTITY_OBSERVATION_ERROR"
        ).values_list("result_code", flat=True)
    )


def failing_receipt(monkeypatch, error=RuntimeError):
    monkeypatch.setattr(
        BrokerIdentityObservation,
        "evidence_for_new_delivery",
        lambda self: (_ for _ in ()).throw(error("x")),
    )


# -- J-01 ---------------------------------------------------------------------


def test_historic_and_off_rows_get_truthful_late(world, kr, payload):
    first = receive(world, None, payload).delivery  # histórica / off
    assert observation_failed_at_receipt(row(first.pk)) is False
    receive(world, None, payload)  # reentrega en off: sin captura ni eventos
    assert keys(first.pk) == []
    receive(world, obs(kr, tok("A")), payload)
    assert keys(first.pk) == ["bi:late"]
    detail = IotMqttDeliveryEvent.objects.get(delivery_id=first.pk, dedupe_key="bi:late")
    assert "no capturada" in detail.safe_detail and "histórica" in detail.safe_detail


def test_observation_failure_is_not_reported_as_historic(world, kr, payload, monkeypatch):
    failing_receipt(monkeypatch)
    first = receive(world, obs(kr, tok("A")), payload).delivery
    monkeypatch.undo()
    assert observation_failed_at_receipt(row(first.pk)) is True
    before = row(first.pk)
    for value in ("A", "B"):
        result = receive(world, obs(kr, tok(value)), payload)
        assert result.status is MqttReceiptStatus.REDELIVERY
    after = row(first.pk)
    assert keys(first.pk) == []  # sin bi:late engañoso ni anomalías inventadas
    assert after.broker_identity_presence is None
    assert after.updated_at == before.updated_at


def test_redelivery_phase_error_does_not_turn_historic_into_not_observed(world, kr, payload):
    first = receive(world, None, payload).delivery
    receive(world, BrokerIdentityObservationFailure("classify", "RuntimeError"), payload)
    assert diags(first.pk) == ["redelivery:classify:RuntimeError"]
    assert observation_failed_at_receipt(row(first.pk)) is False


# -- J-02 ---------------------------------------------------------------------


def _verdict_for(world, kr, payload, identity, register=None, conflict_with=None):
    if register:
        register_identity(
            gateway_uid=world.gateway.uid, token_id=register, keyring=kr, process_id="t"
        )
    delivery = receive(world, identity, payload).delivery
    if conflict_with:
        receive(world, obs(kr, tok(conflict_with)), payload)
    return identity_verdict(row(delivery.pk))


@pytest.mark.parametrize(
    ("props", "register", "conflict", "status", "reason"),
    [
        ((tok("A"),), "A", None, V.MATCH, "match"),
        ((tok("A"),), "Z", None, V.MISMATCH, "mismatch"),
        ((tok("A"),), None, None, V.UNVERIFIABLE, "unregistered"),
        ((), "A", None, V.UNVERIFIABLE, "missing"),
        ((tok("1"), tok("2")), "A", None, V.UNVERIFIABLE, "ambiguous"),
        ((tok(" x"),), "A", None, V.UNVERIFIABLE, "invalid"),
        ((tok("A"),), "A", "B", V.UNVERIFIABLE, "ambiguous"),
    ],
)
def test_explicit_verdict_states(world, kr, payload, props, register, conflict, status, reason):
    verdict = _verdict_for(world, kr, payload, obs(kr, *props), register, conflict)
    assert (verdict.status, verdict.reason) == (status, reason)
    assert verdict.verified is (status is V.MATCH)


def test_not_observed_and_not_applicable_are_never_verified(world, kr, payload, monkeypatch):
    failing_receipt(monkeypatch)
    failed = receive(world, obs(kr, tok("A")), payload).delivery
    monkeypatch.undo()
    historic = receive(world, None, raw_payload(frame_for(world, seq=161))).delivery
    not_observed = identity_verdict(row(failed.pk))
    not_applicable = identity_verdict(row(historic.pk))
    assert (not_observed.status, not_observed.verified) == (V.NOT_OBSERVED, False)
    assert not_observed.would_reject is True  # política futura documentada
    assert (not_applicable.status, not_applicable.verified) == (V.NOT_APPLICABLE, False)


def test_record_mode_logs_not_observed_but_never_rejects(world, kr, payload, monkeypatch):
    monkeypatch.setenv("IOT_BROKER_IDENTITY_MODE", "record")
    failing_receipt(monkeypatch)
    failed = receive(world, obs(kr, tok("A")), payload).delivery
    monkeypatch.undo()
    monkeypatch.setenv("IOT_BROKER_IDENTITY_MODE", "record")
    historic = receive(world, None, raw_payload(frame_for(world, seq=162))).delivery
    while run_mqtt_delivery_worker_once(worker_id="w").processed:
        pass
    for pk, expected in ((failed.pk, ["would_reject_not_observed"]), (historic.pk, [])):
        final = row(pk)
        assert final.state == State.ACCEPTED
        assert (
            list(
                IotMqttDeliveryEvent.objects.filter(
                    delivery_id=pk, event_type="BROKER_IDENTITY_WOULD_REJECT"
                ).values_list("result_code", flat=True)
            )
            == expected
        )


# -- J-03 ---------------------------------------------------------------------


def _finalize():
    while run_mqtt_delivery_worker_once(worker_id="w").processed:
        pass


def test_mismatch_then_postfinal_counts_once(world, kr, payload):
    first = receive(world, obs(kr, tok("A")), payload).delivery
    receive(world, obs(kr, tok("B")), payload)  # mismatch (contado)
    _finalize()
    assert row(first.pk).state == State.ACCEPTED
    for _ in range(3):
        receive(world, obs(kr, tok("B")), payload)  # misma evidencia tras el final
    stored = row(first.pk)
    mism = keys(first.pk, "bi:mismatch:")
    post = keys(first.pk, "bi:postfinal:")
    assert len(mism) == 1 and len(post) == 1
    assert mism[0].split(":", 2)[2] == post[0].split(":", 2)[2]  # misma huella
    assert len(identity_module._counted_evidences(stored)) == 1
    assert stored.broker_identity_overflow == 0 and stored.broker_identity_conflict is True


def test_mismatch_evidence_never_becomes_overflow(world, kr, payload):
    first = receive(world, obs(kr, tok("A")), payload).delivery
    for index in range(5):
        receive(world, obs(kr, tok(f"X{index}")), payload)
    receive(world, obs(kr, tok("X0")), payload)  # repetida con el límite lleno
    assert row(first.pk).broker_identity_overflow == 0
    _finalize()
    receive(world, obs(kr, tok("X0")), payload)  # tras el final: contexto, no overflow
    stored = row(first.pk)
    assert stored.broker_identity_overflow == 0
    assert keys(first.pk, "bi:overflow:") == []
    receive(world, obs(kr, tok("NEW")), payload)  # evidencia distinta: sí cuenta
    stored = row(first.pk)
    assert stored.broker_identity_overflow == 1
    assert len(identity_module._counted_evidences(stored)) == 5  # huellas, no categorías


def test_concurrent_category_change_is_recorded_once(world, kr, payload):
    first = receive(world, obs(kr, tok("A")), payload).delivery
    receive(world, obs(kr, tok("B")), payload)
    _finalize()
    results = _parallel([lambda: receive(world, obs(kr, tok("B")), payload)] * 4)
    _no_errors(results)
    assert all(r.status is MqttReceiptStatus.REDELIVERY for r in results)
    assert len(keys(first.pk, "bi:postfinal:")) == 1
    assert len(identity_module._counted_evidences(row(first.pk))) == 1


# -- J-06 ---------------------------------------------------------------------


def test_observation_diagnostics_are_deduplicated(world, kr, payload, monkeypatch):
    failing_receipt(monkeypatch)
    first = receive(world, obs(kr, tok("A")), payload).delivery
    monkeypatch.undo()
    same = BrokerIdentityObservationFailure("classify", "RuntimeError")
    other = BrokerIdentityObservationFailure("classify", "TypeError")
    for failure in (same, same, same, other, other):
        assert receive(world, failure, payload).status is MqttReceiptStatus.REDELIVERY
    assert sorted(diags(first.pk)) == [
        "receipt:evidence:RuntimeError",
        "redelivery:classify:RuntimeError",
        "redelivery:classify:TypeError",
    ]


def test_database_error_while_recording_diagnostic_blocks_ack(world, kr, payload, monkeypatch):
    first = receive(world, obs(kr, tok("A")), payload).delivery
    monkeypatch.setattr(
        identity_module,
        "record_observation_failure",
        lambda *_a, **_k: (_ for _ in ()).throw(OperationalError("db caída")),
    )
    result = receive(world, BrokerIdentityObservationFailure("classify", "X"), payload)
    assert result.status is MqttReceiptStatus.PERSISTENCE_ERROR
    assert not IotMqttDeliveryEvent.objects.filter(
        delivery_id=first.pk, event_type="REDELIVERY"
    ).exists()
