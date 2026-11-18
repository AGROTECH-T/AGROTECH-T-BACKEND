# ruff: noqa: S101, S105, S106, F811
"""BUILD-FLEPSI-03: escenarios E2E con broker Mosquitto propio, consumidor real
en un proceso hijo y MariaDB desechable del orquestador.

Orden causal: la traza del hijo es secuencial (un contador por proceso, escrito
con fsync) y el hilo de Paho ejecuta en orden ``on_message -> receipt ->
commit_verified (conexión independiente) -> puback_send``. El log del broker
aporta la vista independiente de MQTT (DUP, PUBACK recibido).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import uuid
from pathlib import Path

import pytest
from django.conf import settings as django_settings
from django.db import connection

from apps.iot.models import IotMqttDelivery, IotMqttDeliveryEvent
from apps.iot.services.mqtt_delivery_receipts import build_transport_key
from apps.iot.tests.e2e_flepsi03 import harness
from apps.iot.tests.mqtt_delivery_support import (  # noqa: F401 (fixtures)
    frame_for,
    mqtt_key_settings,
    raw_payload,
    world,
)

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        not harness.OPT_IN.exists(), reason="E2E FLEPSI-03 solo con opt-in explícito"
    ),
]

SESSION_EXPIRY = 600
EVIDENCE: dict[str, dict] = {}


@pytest.fixture(scope="module")
def run_dir():
    directory = harness.new_run_dir()
    yield directory
    report = os.environ.get("AGROTECH_DISPOSABLE_REPORT_DIR")
    if report:
        target = Path(report) / "flepsi03-evidence"
        harness.export_evidence(directory, target)
        (target / "scenarios.json").write_text(
            json.dumps(EVIDENCE, indent=2, sort_keys=True), encoding="utf-8"
        )
    harness.remove_run_dir(directory)


@pytest.fixture(scope="module")
def broker(run_dir):
    instance = harness.start_broker(run_dir)
    yield instance
    instance.stop()
    EVIDENCE["_broker"] = {
        "version": instance.version,
        "host": harness.HOST,
        "port": instance.port,
        "pid": instance.process.pid if instance.process else None,
        "exit_code": instance.process.poll() if instance.process else None,
        "tls": "tlsv1.2, CA de prueba efímera",
        "auth": "password_file con usuarios de prueba; allow_anonymous false",
        "acl": harness.TOPIC_PATTERN,
        "persistence": "true (directorio temporal de la fase)",
    }


@pytest.fixture
def publisher(broker):
    client = harness.Publisher(broker)
    yield client
    client.close()


@pytest.fixture
def children():
    started: list[harness.Child] = []
    yield started
    for child in started:
        child.kill()


def _keys() -> dict[str, str]:
    return {
        name: getattr(django_settings, name)
        for name in (
            "COORDINATOR_KEY_ENCRYPTION_KEY",
            "COORDINATOR_KEY_ENCRYPTION_KEY_VERSION",
            "COORDINATOR_KEY_ENCRYPTION_KEY_PREVIOUS",
            "GATEWAY_CREDENTIAL_HMAC_KEY",
            "SECRET_KEY",
        )
    }


def _start(run_dir, broker, children, *, name, client_id, mode="normal", **kwargs):
    subacks_before = _suback_count(broker, client_id)  # antes de arrancar el hijo
    child = harness.start_child(
        run_dir,
        broker,
        name=name,
        client_id=client_id,
        mode=mode,
        django_settings=_keys(),
        session_expiry_seconds=SESSION_EXPIRY,
        **kwargs,
    )
    children.append(child)
    child.wait_for(lambda ev: any(e["event"] == "on_connect" for e in ev), timeout=40)
    _wait_broker(
        broker,
        rf"Sending SUBACK to {re.escape(client_id)}",
        count=subacks_before + 1,
    )
    return child


def _suback_count(broker, client_id) -> int:
    pattern = re.compile(rf"Sending SUBACK to {re.escape(client_id)}")
    return sum(1 for line in broker.log_lines() if pattern.search(line))


def _wait_broker(broker, pattern: str, count: int = 1, timeout: float = 30.0) -> list[str]:
    import time

    regex = re.compile(pattern)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        lines = [line for line in broker.log_lines() if regex.search(line)]
        if len(lines) >= count:
            return lines
        time.sleep(0.05)
    raise AssertionError(f"log del broker sin {pattern!r} x{count}")


def _broker_lines(broker, pattern: str) -> list[str]:
    regex = re.compile(pattern)
    return [line for line in broker.log_lines() if regex.search(line)]


def _tk(world, payload: bytes) -> bytes:
    return build_transport_key(topic=world.topic, payload=payload)


def _rows(tk: bytes) -> int:
    return IotMqttDelivery.objects.filter(transport_key=tk).count()


def _event_counts(tk: bytes) -> dict[str, int]:
    delivery = IotMqttDelivery.objects.get(transport_key=tk)
    counts: dict[str, int] = {}
    for event_type in IotMqttDeliveryEvent.objects.filter(delivery=delivery).values_list(
        "event_type", flat=True
    ):
        counts[event_type] = counts.get(event_type, 0) + 1
    return counts


def _of(events, name, **match):
    return [
        e for e in events if e["event"] == name and all(e.get(k) == v for k, v in match.items())
    ]


def _seq(events, name, **match) -> int:
    found = _of(events, name, **match)
    assert found, f"falta {name} {match}"
    return found[0]["seq"]


def _done(tk_hex):
    def predicate(events):
        return any(e["event"] == "puback_result" for e in events) and any(
            e["event"] == "receipt_return" and e.get("tk") == tk_hex for e in events
        )

    return predicate


def _puback_after(events, tk_hex) -> dict:
    """Evento puback_send que sigue al commit_verified del mensaje ``tk_hex``."""
    commit = _seq(events, "commit_verified", tk=tk_hex)
    later = [e for e in events if e["event"] == "puback_send" and e["seq"] > commit]
    assert later, "no hubo PUBACK tras el COMMIT"
    return later[0]


def _client_id(tag: str) -> str:
    return f"flepsi03-{tag}-{uuid.uuid4().hex[:6]}"


# -- E2E-01 ---------------------------------------------------------------------------


def test_e2e_01_normal_receipt_commit_before_puback(world, broker, publisher, run_dir, children):
    client_id = _client_id("e01")
    child = _start(run_dir, broker, children, name="e01", client_id=client_id)
    payload = raw_payload(frame_for(world, seq=301))
    tk = _tk(world, payload)
    pub_mid = publisher.publish(world.topic, payload)
    events = child.wait_for(lambda ev: _of(ev, "puback_result"))
    tk_hex = tk.hex()[:16]
    message = _of(events, "on_message")[0]
    order = [
        _seq(events, "on_message"),
        _seq(events, "receipt_enter", tk=tk_hex),
        _seq(events, "receipt_return", tk=tk_hex, status="persisted"),
        _seq(events, "commit_verified", tk=tk_hex, rows_independent_connection=1),
        _seq(events, "puback_send", mid=message["mid"]),
        _seq(events, "puback_result", mid=message["mid"], rc=0),
    ]
    assert order == sorted(order), order
    assert message["qos"] == 1 and message["dup"] == 0
    _wait_broker(broker, rf"Received PUBACK from {re.escape(client_id)} \(Mid: {message['mid']}")
    connected = _broker_lines(broker, rf"as {re.escape(client_id)} \(p5, c0")
    assert connected, "el broker no registró MQTT 5 con clean_start=False"
    assert _rows(tk) == 1
    assert _event_counts(tk) == {"RECEIVED": 1}
    child.stop()
    EVIDENCE["E2E-01"] = {
        "publisher_mid": pub_mid,
        "consumer_mid": message["mid"],
        "tk": tk_hex,
        "causal_seq": order,
        "rows": 1,
        "events": _event_counts(tk),
        "broker_connect": connected[0].split(": ", 1)[-1],
    }


# -- E2E-02 ---------------------------------------------------------------------------


def test_e2e_02_crash_before_commit_is_redelivered_and_persisted(
    world, broker, publisher, run_dir, children
):
    client_id = _client_id("e02")
    first = _start(
        run_dir, broker, children, name="e02a", client_id=client_id, mode="hold_before_commit"
    )
    payload = raw_payload(frame_for(world, seq=302))
    tk = _tk(world, payload)
    tk_hex = tk.hex()[:16]
    publisher.publish(world.topic, payload)
    first.wait_for(lambda ev: _of(ev, "hold", reason="before_commit"))
    assert _rows(tk) == 0  # nada confirmado antes de la caída
    first.kill()
    assert not _broker_lines(broker, rf"Received PUBACK from {re.escape(client_id)}")
    second = _start(run_dir, broker, children, name="e02b", client_id=client_id)
    events = second.wait_for(_done(tk_hex))
    connect = _of(events, "on_connect")[0]
    message = _of(events, "on_message")[0]
    assert connect["session_present"] is True
    assert message["dup"] == 1  # retransmisión MQTT del broker
    assert _of(events, "receipt_return", tk=tk_hex)[0]["status"] == "persisted"
    puback = _puback_after(events, tk_hex)
    _wait_broker(broker, rf"Received PUBACK from {re.escape(client_id)} \(Mid: {puback['mid']}")
    assert _rows(tk) == 1
    assert _event_counts(tk) == {"RECEIVED": 1}
    second.stop()
    EVIDENCE["E2E-02"] = {
        "tk": tk_hex,
        "rows_after_crash": 0,
        "restart_session_present": True,
        "redelivery_dup": message["dup"],
        "final_status": "persisted",
        "rows": 1,
        "events": _event_counts(tk),
    }


# -- E2E-03 ---------------------------------------------------------------------------


def test_e2e_03_persistent_session_recovers_offline_messages(
    world, broker, publisher, run_dir, children
):
    client_id = _client_id("e03")
    first = _start(run_dir, broker, children, name="e03a", client_id=client_id)
    warm = raw_payload(frame_for(world, seq=303))
    publisher.publish(world.topic, warm)
    first.wait_for(_done(_tk(world, warm).hex()[:16]))
    first.stop()  # desconexión controlada (DISCONNECT normal, sesión conservada)
    assert first.process.returncode == 0
    offline = [raw_payload(frame_for(world, seq=304 + i)) for i in range(3)]
    for payload in offline:
        publisher.publish(world.topic, payload)  # el broker los encola para la sesión
    assert all(_rows(_tk(world, p)) == 0 for p in offline)
    second = _start(run_dir, broker, children, name="e03b", client_id=client_id)
    tks = [_tk(world, p).hex()[:16] for p in offline]
    events = second.wait_for(
        lambda ev: all(_of(ev, "receipt_return", tk=t) for t in tks)
        and len(_of(ev, "puback_result")) >= 3
    )
    assert _of(events, "on_connect")[0]["session_present"] is True
    for payload in offline:
        tk = _tk(world, payload)
        assert _rows(tk) == 1 and _event_counts(tk) == {"RECEIVED": 1}
    assert {e["status"] for e in _of(events, "receipt_return")} == {"persisted"}
    second.stop()
    EVIDENCE["E2E-03"] = {
        "session_expiry_seconds": SESSION_EXPIRY,
        "offline_messages": len(offline),
        "restart_session_present": True,
        "recovered_persisted": len(offline),
        "duplicates": 0,
    }


# -- E2E-04 ---------------------------------------------------------------------------


def test_e2e_04_crash_between_commit_and_puback(world, broker, publisher, run_dir, children):
    client_id = _client_id("e04")
    first = _start(
        run_dir, broker, children, name="e04a", client_id=client_id, mode="hold_after_commit"
    )
    payload = raw_payload(frame_for(world, seq=310))
    tk = _tk(world, payload)
    tk_hex = tk.hex()[:16]
    publisher.publish(world.topic, payload)
    held = first.wait_for(lambda ev: _of(ev, "hold", reason="after_commit_before_puback"))
    # COMMIT confirmado desde la conexión de este proceso (independiente del hijo).
    assert _rows(tk) == 1 and _event_counts(tk) == {"RECEIVED": 1}
    assert _of(held, "commit_verified", tk=tk_hex, rows_independent_connection=1)
    assert not _of(held, "puback_send")
    original_mid = _of(held, "on_message")[0]["mid"]
    first.kill()  # TerminateProcess: caída real del proceso, sin PUBACK
    exit_code = first.process.returncode
    assert not _broker_lines(broker, rf"Received PUBACK from {re.escape(client_id)}")
    second = _start(run_dir, broker, children, name="e04b", client_id=client_id)
    events = second.wait_for(_done(tk_hex))
    connect = _of(events, "on_connect")[0]
    message = _of(events, "on_message")[0]
    assert connect["session_present"] is True
    assert message["dup"] == 1
    assert _of(events, "receipt_return", tk=tk_hex)[0]["status"] == "redelivery"
    order = [
        _seq(events, "on_message"),
        _seq(events, "receipt_return", tk=tk_hex, status="redelivery"),
        _seq(events, "commit_verified", tk=tk_hex, rows_independent_connection=1),
        _seq(events, "puback_send", mid=message["mid"]),
    ]
    assert order == sorted(order)
    _wait_broker(broker, rf"Received PUBACK from {re.escape(client_id)} \(Mid: {message['mid']}")
    dup_lines = _broker_lines(broker, rf"Sending PUBLISH to {re.escape(client_id)} \(d1, q1")
    assert dup_lines, "el broker no reenvió con DUP=1"
    assert _rows(tk) == 1
    assert _event_counts(tk) == {"RECEIVED": 1, "REDELIVERY": 1}
    second.stop()
    EVIDENCE["E2E-04"] = {
        "tk": tk_hex,
        "original_mid": original_mid,
        "killed_exit_code": exit_code,
        "puback_before_kill": False,
        "rows_after_kill": 1,
        "redelivery_mid": message["mid"],
        "redelivery_dup": 1,
        "second_status": "redelivery",
        "causal_seq_second": order,
        "rows": 1,
        "events": _event_counts(tk),
        "broker_dup_line": dup_lines[0].split(": ", 1)[-1][:60],
    }


# -- E2E-05 ---------------------------------------------------------------------------


def test_e2e_05_deduplication_new_publication_vs_new_message(
    world, broker, publisher, run_dir, children
):
    client_id = _client_id("e05")
    child = _start(run_dir, broker, children, name="e05", client_id=client_id)
    payload = raw_payload(frame_for(world, seq=320))
    other = raw_payload(frame_for(world, seq=321))
    tk, tk_other = _tk(world, payload), _tk(world, other)
    publisher.publish(world.topic, payload)
    child.wait_for(lambda ev: len(_of(ev, "puback_result")) >= 1)
    publisher.publish(world.topic, payload)  # nueva publicación, mismos bytes
    child.wait_for(lambda ev: len(_of(ev, "puback_result")) >= 2)
    publisher.publish(world.topic, other)  # nuevo mensaje lógico
    events = child.wait_for(lambda ev: len(_of(ev, "puback_result")) >= 3)
    statuses = [e["status"] for e in _of(events, "receipt_return")]
    assert statuses == ["persisted", "redelivery", "persisted"]
    assert [e["dup"] for e in _of(events, "on_message")] == [0, 0, 0]
    assert _rows(tk) == 1 and _event_counts(tk) == {"RECEIVED": 1, "REDELIVERY": 1}
    assert _rows(tk_other) == 1 and _event_counts(tk_other) == {"RECEIVED": 1}
    child.stop()
    EVIDENCE["E2E-05"] = {
        "statuses": statuses,
        "dup_flags": [0, 0, 0],
        "rows_same_payload": 1,
        "rows_new_message": 1,
        "note": "retransmisión MQTT con DUP=1 cubierta en E2E-02 y E2E-04",
    }


# -- E2E-06 ---------------------------------------------------------------------------


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


CASES = [
    ("single", [("token_id", "tok-A")]),
    ("multiple", [("token_id", "t-1"), ("token_id", "t-2")]),
    ("absent", None),
    ("invalid", [("token_id", " bad")]),
    ("single", [("x", "1"), ("token_id", "tok-A"), ("x", "2")]),
]


def test_e2e_06_user_properties_record_and_off(world, broker, publisher, run_dir, children):
    identity_key = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
    client_id = _client_id("e06r")
    child = _start(
        run_dir,
        broker,
        children,
        name="e06r",
        client_id=client_id,
        identity_mode="record",
        identity_key=identity_key,
    )
    assert _of(child.events(), "child_start")[0]["identity_mode"] == "record"
    tks = []
    for index, (_expected, props) in enumerate(CASES):
        payload = raw_payload(frame_for(world, seq=330 + index))
        tks.append(_tk(world, payload))
        publisher.publish(world.topic, payload, user_properties=props)
        child.wait_for(lambda ev, n=index + 1: len(_of(ev, "puback_result")) >= n)
    events = child.events()
    received = _of(events, "on_message")
    stored = []
    for (expected, props), message, tk in zip(CASES, received, tks, strict=True):
        expected_props = None if props is None else [[k, _digest(v)] for k, v in props]
        if props is None:
            assert message["user_properties"] in (None, [])
        else:
            assert message["user_properties"] == expected_props  # orden y repetidos
            assert message["user_properties_type"] == "list"
        delivery = IotMqttDelivery.objects.get(transport_key=tk)
        assert delivery.broker_identity_presence == expected
        assert not IotMqttDeliveryEvent.objects.filter(
            delivery=delivery, dedupe_key__startswith="bi:"
        ).exists()
        stored.append(delivery.broker_identity_presence)
    child.stop()
    off_id = _client_id("e06o")
    off = _start(run_dir, broker, children, name="e06o", client_id=off_id)
    payload = raw_payload(frame_for(world, seq=340))
    publisher.publish(world.topic, payload, user_properties=[("token_id", "tok-A")])
    off_events = off.wait_for(lambda ev: _of(ev, "puback_result"))
    assert _of(off_events, "child_start")[0]["identity_mode"] == "off"
    assert _of(off_events, "on_message")[0]["user_properties"] == [["token_id", _digest("tok-A")]]
    off_delivery = IotMqttDelivery.objects.get(transport_key=_tk(world, payload))
    assert off_delivery.broker_identity_presence is None
    off.stop()
    EVIDENCE["E2E-06"] = {
        "record_presence": stored,
        "order_and_repeats_preserved": True,
        "off_presence": None,
        "bi_events": 0,
    }


# -- E2E-07 ---------------------------------------------------------------------------


def test_e2e_07_persistence_error_has_no_puback_and_recovers(
    world, broker, publisher, run_dir, children
):
    client_id = _client_id("e07")
    child = _start(
        run_dir, broker, children, name="e07", client_id=client_id, mode="kill_db_connection_once"
    )
    payload = raw_payload(frame_for(world, seq=350))
    tk = _tk(world, payload)
    tk_hex = tk.hex()[:16]
    publisher.publish(world.topic, payload)
    ready = child.wait_for(lambda ev: _of(ev, "db_connection_ready"))
    conn_id = _of(ready, "db_connection_ready")[0]["connection_id"]
    with connection.cursor() as cursor:  # fallo real del servidor sobre la conexión del hijo
        cursor.execute("KILL CONNECTION %s", [conn_id])
    child.kill_done_file.write_text("killed", encoding="ascii")
    events = child.wait_for(
        lambda ev: _of(ev, "receipt_return", tk=tk_hex, status="persisted")
        and _of(ev, "puback_result"),
        timeout=60,
    )
    failure = _of(events, "receipt_return", tk=tk_hex, status="persistence_error")
    assert failure, "no se observó PERSISTENCE_ERROR"
    fail_seq = failure[0]["seq"]
    reconnect = [e for e in _of(events, "on_connect") if e["seq"] > fail_seq]
    assert reconnect and reconnect[0]["session_present"] is True
    between = [e for e in events if fail_seq < e["seq"] < reconnect[0]["seq"]]
    assert not [e for e in between if e["event"] == "puback_send"]  # sin PUBACK
    assert not [e for e in events if e["event"] == "puback_send" and e["seq"] < fail_seq]
    redelivered = [e for e in _of(events, "on_message") if e["seq"] > reconnect[0]["seq"]]
    assert redelivered and redelivered[0]["dup"] == 1
    _puback_after(events, tk_hex)
    assert _rows(tk) == 1 and _event_counts(tk) == {"RECEIVED": 1}
    child_logs = child.log.read_text(encoding="utf-8") + (
        child.log.parent / "stderr.txt"
    ).read_text(encoding="utf-8", errors="replace")
    errnos = re.findall(r"errno=(\d+|None)", child_logs)
    assert errnos and errnos[0] != "None", errnos
    child.stop()
    EVIDENCE["E2E-07"] = {
        "tk": tk_hex,
        "killed_db_connection_id": conn_id,
        "first_status": "persistence_error",
        "error_code": failure[0]["error_code"],
        "mariadb_errno": errnos[0],
        "puback_before_reconnect": False,
        "reconnect_session_present": True,
        "redelivery_dup": 1,
        "final_status": "persisted",
        "rows": 1,
        "events": _event_counts(tk),
    }
