# ruff: noqa: S101, S105, S106, F811
"""BUILD-FLEPSI-02K-CORRECCIONES: READ COMMITTED (K-H1), deduplicación
concurrente de diagnósticos NULL (K-H2), contexto overflow -> final (K-H3),
``result_code`` acotado sin fusiones (K-H4) y límites reales de 5 y 64.

Contra MariaDB 11.8 desechable. El límite de 64 se usa con su valor real:
ninguna prueba de este archivo parchea ``MAX_OVERFLOW_EVIDENCES``.
"""

from __future__ import annotations

import base64
import secrets
from types import SimpleNamespace

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.db import OperationalError, connection

import apps.iot.db_guard as db_guard
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
    diagnostic_result_code,
    observation_failed_at_receipt,
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
from config.database import database

pytestmark = pytest.mark.django_db(transaction=True)

REAL_OVERFLOW_CAP = 64
OBS_ERROR = "BROKER_IDENTITY_OBSERVATION_ERROR"


@pytest.fixture
def kr():
    key = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
    return build_keyring(raw_key=key, raw_version="1", raw_previous=None, raw_next=None)


@pytest.fixture
def payload(world):
    return raw_payload(frame_for(world, seq=170))


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


def row(pk):
    return IotMqttDelivery.objects.get(pk=pk)


def keys(pk, prefix):
    return list(
        IotMqttDeliveryEvent.objects.filter(
            delivery_id=pk, dedupe_key__startswith=prefix
        ).values_list("dedupe_key", flat=True)
    )


def diags(pk):
    return sorted(
        IotMqttDeliveryEvent.objects.filter(delivery_id=pk, event_type=OBS_ERROR).values_list(
            "result_code", flat=True
        )
    )


def counted(pk):
    return identity_module._counted_evidences(row(pk))


def finalize():
    while run_mqtt_delivery_worker_once(worker_id="w").processed:
        pass


def ok(result):
    assert result.status is MqttReceiptStatus.REDELIVERY, result


def with_counted_limit(world, kr, payload):
    """Entrega con identidad A y 5 huellas distintas contadas (M0..M4)."""
    first = receive(world, obs(kr, "A"), payload).delivery
    for index in range(5):
        ok(receive(world, obs(kr, f"M{index}"), payload))
    return first


def fill_overflow(world, kr, payload, first, count, prefix="O"):
    for index in range(count):
        ok(receive(world, obs(kr, f"{prefix}{index}"), payload))
        stored = row(first.pk)
        # Invariante de atomicidad: contador == eventos de overflow persistidos.
        assert stored.broker_identity_overflow == len(keys(first.pk, "bi:overflow:"))


def failing_receipt(monkeypatch, error=RuntimeError):
    monkeypatch.setattr(
        BrokerIdentityObservation,
        "evidence_for_new_delivery",
        lambda self: (_ for _ in ()).throw(error("x")),
    )


# -- K-H1: READ COMMITTED -------------------------------------------------------


def test_project_mysql_config_declares_read_committed(tmp_path, monkeypatch):
    for name in ("DATABASE_URL", "DB_SSL_MODE", "DB_SSL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DB_ENGINE", "mysql")
    options = database(tmp_path)["OPTIONS"]
    assert options["isolation_level"] == "read committed"
    assert options["charset"] == "utf8mb4"


def test_disposable_connection_effectively_uses_read_committed():
    assert db_guard.effective_isolation_level() == "read committed"
    db_guard.require_read_committed()
    with connection.cursor() as cursor:
        cursor.execute("SELECT @@session.transaction_isolation")
        assert cursor.fetchone()[0] == "READ-COMMITTED"


def _fake_connections(monkeypatch, options, vendor="mysql"):
    fake = SimpleNamespace(vendor=vendor, settings_dict={"OPTIONS": options})
    monkeypatch.setattr(db_guard, "connections", {"default": fake})


@pytest.mark.parametrize("level", ["repeatable read", "serializable", "read uncommitted"])
def test_guard_rejects_other_isolation_levels(monkeypatch, level):
    _fake_connections(monkeypatch, {"isolation_level": level})
    with pytest.raises(ImproperlyConfigured, match="READ COMMITTED"):
        db_guard.require_read_committed()


def test_guard_accepts_django_default_and_ignores_other_vendors(monkeypatch):
    _fake_connections(monkeypatch, {})
    db_guard.require_read_committed()  # sin clave: Django aplica read committed
    _fake_connections(monkeypatch, {}, vendor="sqlite")
    assert db_guard.effective_isolation_level() is None


@pytest.mark.parametrize("command", ["mqtt_consumer", "mqtt_delivery_worker"])
def test_iot_processes_refuse_repeatable_read(monkeypatch, command):
    monkeypatch.delenv("IOT_BROKER_IDENTITY_MODE", raising=False)
    _fake_connections(monkeypatch, {"isolation_level": "repeatable read"})
    with pytest.raises(ImproperlyConfigured, match="READ COMMITTED"):
        call_command(command)


# -- K-H2: diagnósticos NULL concurrentes ------------------------------------------


def test_concurrent_identical_diagnostics_are_stored_once(world, kr, payload):
    first = receive(world, obs(kr, "A"), payload).delivery
    failure = BrokerIdentityObservationFailure("classify", "RuntimeError")
    results = _parallel([lambda: receive(world, failure, payload)] * 6)
    _no_errors(results)
    for result in results:
        ok(result)
    assert diags(first.pk) == ["redelivery:classify:RuntimeError"]
    stored = row(first.pk)
    assert stored.state == State.RECEIVED and stored.broker_identity_presence is not None
    assert (
        IotMqttDeliveryEvent.objects.filter(delivery_id=first.pk, event_type="REDELIVERY").count()
        == 6
    )


def test_concurrent_distinct_diagnostics_keep_one_per_code(world, kr, payload):
    first = receive(world, obs(kr, "A"), payload).delivery
    failures = [
        BrokerIdentityObservationFailure(stage, error)
        for stage, error in (("classify", "RuntimeError"), ("classify", "TypeError"))
        for _ in range(3)
    ] + [BrokerIdentityObservationFailure("record", "KeyError")] * 2
    results = _parallel([lambda f=f: receive(world, f, payload) for f in failures])
    _no_errors(results)
    for result in results:
        ok(result)
    assert diags(first.pk) == [
        "redelivery:classify:RuntimeError",
        "redelivery:classify:TypeError",
        "redelivery:record:KeyError",
    ]


def test_concurrent_first_receipt_with_failed_observation(world, kr, monkeypatch):
    # BUILD-FLEPSI-02M (M-01): tras 02L la carrera no admite PERSISTENCE_ERROR
    # ni se simula ninguna reentrega; cada repetición es una primera recepción.
    failing_receipt(monkeypatch)
    for repetition in range(5):
        payload = raw_payload(frame_for(world, seq=180 + repetition))
        identities = [obs(kr, "A") for _ in range(4)]
        results = _parallel([lambda i=i, p=payload: receive(world, i, p) for i in identities])
        _no_errors(results)
        statuses = [r.status for r in results]
        assert MqttReceiptStatus.PERSISTENCE_ERROR not in statuses, statuses
        assert statuses.count(MqttReceiptStatus.PERSISTED) == 1, statuses
        assert statuses.count(MqttReceiptStatus.REDELIVERY) == 3, statuses
        assert len({r.delivery.pk for r in results}) == 1
        delivery = row(results[0].delivery.pk)
        assert IotMqttDelivery.objects.filter(transport_key=delivery.transport_key).count() == 1
        events = IotMqttDeliveryEvent.objects.filter(delivery_id=delivery.pk)
        assert events.filter(event_type="RECEIVED").count() == 1
        assert events.filter(event_type="REDELIVERY").count() == 3
        # 02J: observación fallida en la recepción, sin bi:late ni anomalías.
        assert delivery.broker_identity_presence is None
        assert diags(delivery.pk) == ["receipt:evidence:RuntimeError"]
        assert observation_failed_at_receipt(delivery) is True
        assert keys(delivery.pk, "bi:") == []


# -- K-H3: overflow seguido de estado final ---------------------------------------


def test_overflow_then_final_records_one_context_without_counting(world, kr, payload):
    first = with_counted_limit(world, kr, payload)
    ok(receive(world, obs(kr, "OV"), payload))
    assert row(first.pk).broker_identity_overflow == 1
    finalize()
    assert row(first.pk).state == State.ACCEPTED
    for _ in range(3):  # misma evidencia + reentregas tras crear el contexto
        ok(receive(world, obs(kr, "OV"), payload))
    stored = row(first.pk)
    post = IotMqttDeliveryEvent.objects.filter(
        delivery_id=first.pk, dedupe_key__startswith="bi:postfinal:"
    )
    assert post.count() == 1
    context = post.get()
    assert "como overflow" in context.safe_detail
    overflow_key = keys(first.pk, "bi:overflow:")[0]
    assert context.dedupe_key.split(":", 2)[2] == overflow_key.split(":", 2)[2]
    assert stored.broker_identity_overflow == 1
    assert len(counted(first.pk)) == 5  # la huella overflow no consume el límite
    ok(receive(world, obs(kr, "NEW"), payload))  # evidencia distinta: overflow normal
    assert row(first.pk).broker_identity_overflow == 2
    assert len(counted(first.pk)) == 5


def test_concurrent_overflow_category_change_records_once(world, kr, payload):
    first = with_counted_limit(world, kr, payload)
    ok(receive(world, obs(kr, "OV"), payload))
    finalize()
    results = _parallel([lambda: receive(world, obs(kr, "OV"), payload)] * 5)
    _no_errors(results)
    for result in results:
        ok(result)
    assert len(keys(first.pk, "bi:postfinal:")) == 1
    assert row(first.pk).broker_identity_overflow == 1
    assert len(counted(first.pk)) == 5


# -- K-H4: result_code acotado -------------------------------------------------


def test_result_code_short_values_are_unchanged():
    assert diagnostic_result_code("receipt", "evidence", "RuntimeError") == (
        "receipt:evidence:RuntimeError"
    )


def test_long_result_codes_with_shared_prefix_stay_distinct():
    base = "E" * 80
    first = diagnostic_result_code("redelivery", "classify", base + "One")
    second = diagnostic_result_code("redelivery", "classify", base + "Two")
    assert first != second
    assert len(first) == len(second) == 64
    assert first.startswith("redelivery:classify:E") and "~" in first
    assert diagnostic_result_code("redelivery", "classify", base + "One") == first
    exact = "X" * (64 - len("receipt:evidence:"))
    assert diagnostic_result_code("receipt", "evidence", exact) == f"receipt:evidence:{exact}"


def test_long_error_types_are_stored_and_deduplicated(world, kr, payload, monkeypatch):
    long_error = type("Fallo" + "X" * 90, (RuntimeError,), {})
    failing_receipt(monkeypatch, error=long_error)
    first = receive(world, obs(kr, "A"), payload).delivery
    monkeypatch.undo()
    assert observation_failed_at_receipt(row(first.pk)) is True
    base = "Y" * 80
    for suffix in ("One", "Two", "One", "Two"):
        ok(receive(world, BrokerIdentityObservationFailure("classify", base + suffix), payload))
    codes = diags(first.pk)
    assert len(codes) == 3 and all(len(code) <= 64 for code in codes)
    assert sum(code.startswith("redelivery:classify:") for code in codes) == 2


# -- Límite de cinco (huellas distintas) ---------------------------------------------


def test_limit_of_five_distinct_fingerprints(world, kr, payload):
    first = with_counted_limit(world, kr, payload)
    assert len(counted(first.pk)) == 5
    assert row(first.pk).broker_identity_overflow == 0
    non_redelivery = IotMqttDeliveryEvent.objects.filter(delivery_id=first.pk).exclude(
        event_type="REDELIVERY"
    )
    events_before = non_redelivery.count()
    for index in range(5):  # retransmisiones de evidencias contadas
        ok(receive(world, obs(kr, f"M{index}"), payload))
    assert len(keys(first.pk, "bi:mismatch:")) == 5
    assert non_redelivery.count() == events_before  # sin eventos ni conteo nuevos
    ok(receive(world, obs(kr, "SIXTH"), payload))  # sexta distinta -> overflow
    stored = row(first.pk)
    assert len(counted(first.pk)) == 5
    assert stored.broker_identity_overflow == 1
    assert len(keys(first.pk, "bi:limit")) == 1
    finalize()
    for index in range(5):  # cambio de categoría: contexto sin recontar
        ok(receive(world, obs(kr, f"M{index}"), payload))
    assert len(keys(first.pk, "bi:postfinal:")) == 5
    assert len(counted(first.pk)) == 5
    assert row(first.pk).broker_identity_overflow == 1


def test_limit_of_five_under_concurrency(world, kr, payload):
    first = receive(world, obs(kr, "A"), payload).delivery
    for index in range(3):
        ok(receive(world, obs(kr, f"M{index}"), payload))
    values = ["C", "C", "D", "D", "E", "F", "M0", "M1"]
    results = _parallel([lambda v=v: receive(world, obs(kr, v), payload) for v in values])
    _no_errors(results)
    for result in results:
        ok(result)
    stored = row(first.pk)
    assert len(counted(first.pk)) == 5  # 3 + 2 de {C, D, E, F}
    assert len(keys(first.pk, "bi:mismatch:")) == 5
    assert stored.broker_identity_overflow == 2  # las otras 2 de {C, D, E, F}
    assert len(keys(first.pk, "bi:overflow:")) == 2


def test_limit_of_five_rollback_keeps_events_and_flag_consistent(world, kr, payload, monkeypatch):
    first = receive(world, obs(kr, "A"), payload).delivery
    real = identity_module._create_event

    def fail_on_mismatch(row_, dedupe_key, *args):
        if dedupe_key.startswith("bi:mismatch:"):
            raise OperationalError("db caída")
        return real(row_, dedupe_key, *args)

    monkeypatch.setattr(identity_module, "_create_event", fail_on_mismatch)
    result = receive(world, obs(kr, "M0"), payload)
    assert result.status is MqttReceiptStatus.PERSISTENCE_ERROR
    stored = row(first.pk)
    assert stored.broker_identity_conflict is False and counted(first.pk) == set()
    monkeypatch.undo()
    ok(receive(world, obs(kr, "M0"), payload))
    assert row(first.pk).broker_identity_conflict is True and len(counted(first.pk)) == 1


# -- Límite real de 64 (sin parche) -------------------------------------------------


def test_real_overflow_cap_is_64_and_unpatched():
    assert identity_module.MAX_OVERFLOW_EVIDENCES == REAL_OVERFLOW_CAP


def test_real_overflow_cap_of_64(world, kr, payload):
    assert identity_module.MAX_OVERFLOW_EVIDENCES == REAL_OVERFLOW_CAP
    first = with_counted_limit(world, kr, payload)
    assert row(first.pk).broker_identity_overflow == 0  # A: estado inicial
    fill_overflow(world, kr, payload, first, 63)  # B: 1..63
    assert row(first.pk).broker_identity_overflow == 63
    assert keys(first.pk, "bi:overflowcap") == []
    ok(receive(world, obs(kr, "O63"), payload))  # C: la 64.ª se almacena
    assert row(first.pk).broker_identity_overflow == 64
    assert len(keys(first.pk, "bi:overflow:")) == 64
    assert keys(first.pk, "bi:overflowcap") == []
    ok(receive(world, obs(kr, "O64"), payload))  # D: la 65.ª no suma
    assert row(first.pk).broker_identity_overflow == 64
    assert len(keys(first.pk, "bi:overflow:")) == 64
    assert keys(first.pk, "bi:overflowcap") == ["bi:overflowcap"]
    for value in ("O0", "O31", "O63", "O64", "O65", "M0"):  # E: repeticiones y nuevas
        ok(receive(world, obs(kr, value), payload))
    stored = row(first.pk)
    assert stored.broker_identity_overflow == 64
    assert len(keys(first.pk, "bi:overflow:")) == 64
    assert len(keys(first.pk, "bi:overflowcap")) == 1
    assert len(counted(first.pk)) == 5


def test_real_overflow_cap_under_concurrency(world, kr, payload):
    assert identity_module.MAX_OVERFLOW_EVIDENCES == REAL_OVERFLOW_CAP
    first = with_counted_limit(world, kr, payload)
    fill_overflow(world, kr, payload, first, 62)
    values = ["P", "Q", "R", "S", "P", "T", "O0"]  # 5 nuevas distintas para 2 huecos
    results = _parallel([lambda v=v: receive(world, obs(kr, v), payload) for v in values])
    _no_errors(results)
    for result in results:
        ok(result)
    stored = row(first.pk)
    assert stored.broker_identity_overflow == 64
    assert len(keys(first.pk, "bi:overflow:")) == 64
    assert len(keys(first.pk, "bi:overflowcap")) == 1
    more = _parallel([lambda v=v: receive(world, obs(kr, v), payload) for v in "UVWXY"])
    _no_errors(more)
    assert row(first.pk).broker_identity_overflow == 64
    assert len(keys(first.pk, "bi:overflowcap")) == 1


def test_real_overflow_cap_atomicity(world, kr, payload, monkeypatch):
    assert identity_module.MAX_OVERFLOW_EVIDENCES == REAL_OVERFLOW_CAP
    first = with_counted_limit(world, kr, payload)
    fill_overflow(world, kr, payload, first, 63)
    real = identity_module._create_event

    def failing(prefix):
        def create(row_, dedupe_key, *args):
            if dedupe_key.startswith(prefix):
                raise OperationalError("db caída")
            return real(row_, dedupe_key, *args)

        return create

    monkeypatch.setattr(identity_module, "_create_event", failing("bi:overflow:"))
    result = receive(world, obs(kr, "LAST"), payload)
    assert result.status is MqttReceiptStatus.PERSISTENCE_ERROR
    assert row(first.pk).broker_identity_overflow == 63
    assert len(keys(first.pk, "bi:overflow:")) == 63
    monkeypatch.undo()
    ok(receive(world, obs(kr, "LAST"), payload))
    assert row(first.pk).broker_identity_overflow == 64
    assert len(keys(first.pk, "bi:overflow:")) == 64
    monkeypatch.setattr(identity_module, "_create_event", failing("bi:overflowcap"))
    result = receive(world, obs(kr, "OVER"), payload)
    assert result.status is MqttReceiptStatus.PERSISTENCE_ERROR
    assert keys(first.pk, "bi:overflowcap") == []
    assert row(first.pk).broker_identity_overflow == 64
    monkeypatch.undo()
    ok(receive(world, obs(kr, "OVER"), payload))
    assert len(keys(first.pk, "bi:overflowcap")) == 1
    assert row(first.pk).broker_identity_overflow == len(keys(first.pk, "bi:overflow:")) == 64
