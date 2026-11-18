# ruff: noqa: S101, S105, S106
"""BUILD-IOT-BACKEND-INTEGRATION-01: failover sources/sensors through the real
durable MQTT path (local Mosquitto owned by the test -> real consumer process ->
durable receipt -> worker -> ingestion -> measurements) on the disposable DB.

Opt-in only (same OPT_IN file as the FLEPSI-03 E2E). No Flespi, no external
services. Data lives only in the disposable MariaDB of the orchestrator.
"""

from __future__ import annotations

import base64
import json
import os
import re
import ssl
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import paho.mqtt.client as mqtt
import pytest
from django.conf import settings as django_settings
from django.db import connection

import apps.iot.services as iot
from apps.iot.models import (
    Coordinator,
    CoordinatorCredential,
    Gateway,
    IotMqttDelivery,
    IotMqttDeliveryEvent,
    Measurement,
    ReceivedFrame,
    Sensor,
    SensorSource,
)
from apps.iot.services.mqtt_delivery_receipts import build_transport_key
from apps.iot.services.mqtt_delivery_worker import run_mqtt_delivery_worker_once
from apps.iot.tests import lora_vectors
from apps.iot.tests.e2e_flepsi03 import harness
from apps.iot.tests.mqtt_delivery_support import mqtt_key_settings  # noqa: F401

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(not harness.OPT_IN.exists(), reason="E2E solo con opt-in explícito"),
]

EVIDENCE: dict[str, object] = {}
BOOT = 7
BME = ((1, 1, 2534), (2, 2, 6325))  # BME680: 25.34 C, 63.25 %RH
DHT = ((3, 1, 3010), (4, 2, 7060))  # DHT11: 30.10 C, 70.60 %RH


@dataclass
class Node:
    gateway: Gateway
    coordinator: Coordinator
    credential: CoordinatorCredential
    key: bytes

    @property
    def topic(self) -> str:
        return f"agrotech/v1/gw/{self.gateway.uid}/up"


def _source(admin, farm_id, coordinator, name, model, role):
    return iot.create_sensor_source(
        user=admin,
        farm_id=farm_id,
        coordinator_uid=coordinator.uid,
        name=name,
        model=model,
        role=role,
    )


@pytest.fixture
def node(mqtt_key_settings, admin, farm_id) -> Node:  # noqa: F811
    """Farm -> Coordinator -> Gateway -> CoordinatorGateway -> credentials ->
    2 sources -> 4 sensors, created ONLY through the existing domain services."""
    gateway = iot.create_gateway(user=admin, farm_id=farm_id, name="Gateway")
    coordinator = iot.create_coordinator(user=admin, farm_id=farm_id, name="Coordinador")
    iot.authorize_gateway(
        user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid, gateway_uid=gateway.uid
    )
    iot.issue_gateway_credential(user=admin, farm_id=farm_id, gateway_uid=gateway.uid)
    issued = iot.issue_coordinator_credential(
        user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid
    )
    bme = _source(admin, farm_id, coordinator, "BME680", "BME680", "primary")
    dht = _source(admin, farm_id, coordinator, "DHT11", "DHT11", "backup_comparison")
    for channel, metric, source in (
        (1, "temperature", bme),
        (2, "relative_humidity", bme),
        (3, "temperature", dht),
        (4, "relative_humidity", dht),
    ):
        iot.create_sensor(
            user=admin,
            farm_id=farm_id,
            coordinator_uid=coordinator.uid,
            source_uid=source.uid,
            channel=channel,
            metric=metric,
        )
    return Node(gateway, coordinator, issued.credential, issued.key)


@pytest.fixture(scope="module")
def run_dir():
    directory = harness.new_run_dir()
    yield directory
    report = os.environ.get("AGROTECH_DISPOSABLE_REPORT_DIR")
    if report:
        target = Path(report) / "backend-integration-01"
        harness.export_evidence(directory, target)
        (target / "scenarios.json").write_text(
            json.dumps(EVIDENCE, indent=2, sort_keys=True, default=str), encoding="utf-8"
        )
    harness.remove_run_dir(directory)


@pytest.fixture(scope="module")
def broker(run_dir):
    instance = harness.start_broker(run_dir)
    yield instance
    instance.stop()


class Publisher311:
    """Gateway-like publisher: MQTT 3.1.1, QoS 1, retain=false, TLS."""

    def __init__(self, broker) -> None:
        self.client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"bi01-pub-{uuid.uuid4().hex[:8]}",
            protocol=mqtt.MQTTv311,
            clean_session=True,
        )
        self.client.username_pw_set(broker.publisher_user, broker.publisher_password)
        self.client.tls_set(
            ca_certs=str(broker.ca_certs),
            cert_reqs=ssl.CERT_REQUIRED,
            tls_version=ssl.PROTOCOL_TLS_CLIENT,
        )
        self.client.connect(harness.HOST, broker.port, keepalive=15)
        self.client.loop_start()

    def publish(self, topic: str, payload: bytes) -> None:
        info = self.client.publish(topic, payload, qos=1, retain=False)
        info.wait_for_publish(harness.TIMEOUT)
        assert info.is_published()

    def close(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()


@pytest.fixture
def consumer(mqtt_key_settings, run_dir, broker):  # noqa: F811
    client_id = f"bi01-consumer-{uuid.uuid4().hex[:6]}"
    keys = {
        name: getattr(django_settings, name)
        for name in (
            "COORDINATOR_KEY_ENCRYPTION_KEY",
            "COORDINATOR_KEY_ENCRYPTION_KEY_VERSION",
            "COORDINATOR_KEY_ENCRYPTION_KEY_PREVIOUS",
            "GATEWAY_CREDENTIAL_HMAC_KEY",
            "SECRET_KEY",
        )
    }
    subacks = len(_broker_lines(broker, rf"Sending SUBACK to {re.escape(client_id)}"))
    child = harness.start_child(
        run_dir, broker, name=client_id, client_id=client_id, mode="normal", django_settings=keys
    )
    child.wait_for(lambda ev: any(e["event"] == "on_connect" for e in ev), timeout=40)
    _wait(lambda: len(_broker_lines(broker, rf"Sending SUBACK to {re.escape(client_id)}")) > subacks)
    yield child
    child.stop()


@pytest.fixture
def publisher(broker):
    client = Publisher311(broker)
    yield client
    client.close()


def _broker_lines(broker, pattern: str) -> list[str]:
    regex = re.compile(pattern)
    return [line for line in broker.log_lines() if regex.search(line)]


def _wait(predicate, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("condición no alcanzada")


def _frame(node: Node, *, seq: int, readings, boot: int = BOOT, key: bytes | None = None):
    return lora_vectors.frame(
        uid=node.coordinator.uid,
        key=node.key if key is None else key,
        key_version=node.credential.key_version,
        boot_counter=boot,
        seq=seq,
        readings=readings,
    )


def _payload(frame: bytes, *, rssi: int = -87, extra: dict | None = None) -> bytes:
    document = {
        "v": 1,
        "frame": base64.b64encode(frame).decode("ascii"),
        "lora_rx_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace(
            "+00:00", "Z"
        ),
        "clock_synced": True,
        "buffered": False,
        "rssi": rssi,
        "snr": 7.5,
    }
    document.update(extra or {})
    return json.dumps(document, separators=(",", ":")).encode("utf-8")


def _deliver(publisher, consumer, topic: str, payload: bytes) -> IotMqttDelivery:
    """Publish, wait for the durable receipt + PUBACK, then run the worker."""
    tk = build_transport_key(topic=topic, payload=payload)
    tk_hex = tk.hex()[:16]
    before = len([e for e in consumer.events() if e["event"] == "puback_result"])
    publisher.publish(topic, payload)
    events = consumer.wait_for(
        lambda ev: any(e["event"] == "receipt_return" and e.get("tk") == tk_hex for e in ev)
        and len([e for e in ev if e["event"] == "puback_result"]) > before
    )
    order = [
        next(e["seq"] for e in events if e["event"] == "receipt_return" and e.get("tk") == tk_hex),
        next(
            e["seq"]
            for e in events
            if e["event"] == "commit_verified" and e.get("tk") == tk_hex
        ),
    ]
    puback = [e for e in events if e["event"] == "puback_send" and e["seq"] > order[1]]
    assert puback, "no hubo PUBACK tras el COMMIT"
    assert order[0] < order[1] < puback[0]["seq"]  # receipt -> commit verified -> PUBACK
    while run_mqtt_delivery_worker_once(worker_id="bi01-worker").processed:
        pass
    return IotMqttDelivery.objects.get(transport_key=tk)


def _frame_measurements(node: Node, seq: int, boot: int = BOOT) -> list[dict]:
    frame = ReceivedFrame.objects.filter(
        coordinator=node.coordinator, boot_counter=boot, seq=seq
    ).first()
    if frame is None:
        return []
    return [
        {
            "channel": m.channel,
            "metric": m.metric,
            "value": str(m.value),
            "sensor_uid": m.sensor.uid,
            "source": m.sensor.source.name,
            "role": m.sensor.source.role,
        }
        for m in Measurement.objects.filter(frame=frame)
        .select_related("sensor__source")
        .order_by("channel")
    ]


def test_00_disposable_isolation():
    from config.settings_test_disposable_iot import validate_live_iot_disposable_database

    validate_live_iot_disposable_database(connection)  # raises if not the disposable DB
    settings_dict = connection.settings_dict
    with connection.cursor() as cursor:
        cursor.execute("SELECT DATABASE(), @@hostname, @@version")
        database, hostname, version = cursor.fetchone()
    assert settings_dict["NAME"].startswith("agrotech_permtest_")
    assert settings_dict["NAME"] not in ("agrotech_db", "agrotech_t")
    assert str(settings_dict["PORT"]) == "3318"
    assert str(settings_dict["PORT"]) not in ("3306", "3307")
    assert database == settings_dict["NAME"] and hostname.startswith("agrotech-permtest-")
    EVIDENCE["isolation"] = {
        "engine": settings_dict["ENGINE"],
        "name": database,
        "host": settings_dict["HOST"],
        "port": settings_dict["PORT"],
        "server_hostname": hostname,
        "server_version": version,
        "settings_module": os.environ.get("DJANGO_SETTINGS_MODULE"),
    }


def test_01_sources_and_sensors(node):
    sources = {s.name: s for s in SensorSource.objects.filter(coordinator=node.coordinator)}
    assert set(sources) == {"BME680", "DHT11"}
    assert sources["BME680"].role == "primary" and sources["DHT11"].role == "backup_comparison"
    sensors = {
        s.channel: s
        for s in Sensor.objects.filter(coordinator=node.coordinator, is_active=True).select_related(
            "source"
        )
    }
    expected = {
        1: ("temperature", "BME680"),
        2: ("relative_humidity", "BME680"),
        3: ("temperature", "DHT11"),
        4: ("relative_humidity", "DHT11"),
    }
    assert {c: (s.metric, s.source.name) for c, s in sensors.items()} == expected
    assert all(s.farm_id == node.coordinator.farm_id for s in sensors.values())
    assert all(s.source.farm_id == node.coordinator.farm_id for s in sensors.values())
    EVIDENCE["sources"] = {n: s.role for n, s in sources.items()}
    EVIDENCE["sensors"] = {c: [s.metric, s.source.name, s.uid] for c, s in sensors.items()}


def test_02_e1_e2_e3_sources_keep_identity(node, consumer, publisher):
    results = {}
    for label, seq, readings, source in (
        ("E1", 101, BME, "BME680"),
        ("E2", 102, DHT, "DHT11"),
        ("E3-bme", 103, BME, "BME680"),
        ("E3-dht", 104, DHT, "DHT11"),
    ):
        delivery = _deliver(publisher, consumer, node.topic, _payload(_frame(node, seq=seq, readings=readings)))
        assert delivery.state == "ACCEPTED", (label, delivery.state, delivery.result_code)
        rows = _frame_measurements(node, seq)
        channels = [r[0] for r in readings]
        assert [r["channel"] for r in rows] == channels
        assert all(r["source"] == source for r in rows)
        expected_values = {1: "25.34", 2: "63.25", 3: "30.10", 4: "70.60"}
        assert all(r["value"] == expected_values[r["channel"]] for r in rows)
        results[label] = {"state": delivery.state, "result_code": delivery.result_code, "rows": rows}
    EVIDENCE["E1_E2_E3"] = results


def test_03_e4_unknown_channel_and_e5_incomplete_mapping(node, consumer, publisher, admin, farm_id):
    unknown = _deliver(
        publisher,
        consumer,
        node.topic,
        _payload(_frame(node, seq=105, readings=((5, 1, 2500), (2, 2, 6000)))),
    )
    assert unknown.state == "REJECTED" and unknown.result_code == "rejected_sensor_mapping"
    assert _frame_measurements(node, 105) == []
    assert Measurement.objects.filter(channel=5).count() == 0

    # E5: a second coordinator whose backup pair lacks the channel 4 mapping.
    coordinator2 = iot.create_coordinator(user=admin, farm_id=farm_id, name="Coordinador 2")
    iot.authorize_gateway(
        user=admin, farm_id=farm_id, coordinator_uid=coordinator2.uid, gateway_uid=node.gateway.uid
    )
    issued2 = iot.issue_coordinator_credential(
        user=admin, farm_id=farm_id, coordinator_uid=coordinator2.uid
    )
    dht2 = _source(admin, farm_id, coordinator2, "DHT11", "DHT11", "backup_comparison")
    iot.create_sensor(
        user=admin,
        farm_id=farm_id,
        coordinator_uid=coordinator2.uid,
        source_uid=dht2.uid,
        channel=3,
        metric="temperature",
    )
    node2 = Node(node.gateway, coordinator2, issued2.credential, issued2.key)
    incomplete = _deliver(publisher, consumer, node.topic, _payload(_frame(node2, seq=1, readings=DHT)))
    assert incomplete.state == "REJECTED" and incomplete.result_code == "rejected_sensor_mapping"
    assert _frame_measurements(node2, 1) == []
    EVIDENCE["E4"] = {"state": unknown.state, "result_code": unknown.result_code}
    EVIDENCE["E5"] = {"state": incomplete.state, "result_code": incomplete.result_code}


def test_04_e6_redelivery_and_duplicate_frame(node, consumer, publisher):
    frame = _frame(node, seq=106, readings=BME)
    document = json.loads(_payload(frame))
    payload = json.dumps(document, separators=(",", ":")).encode("utf-8")
    first = _deliver(publisher, consumer, node.topic, payload)
    assert first.state == "ACCEPTED"
    # 1) Identical MQTT message (same transport key): same durable row, REDELIVERY event.
    again = _deliver(publisher, consumer, node.topic, payload)
    assert again.pk == first.pk and again.state == "ACCEPTED"
    assert IotMqttDeliveryEvent.objects.filter(delivery=first, event_type="REDELIVERY").count() == 1
    # 2) Same frame and same envelope values, different JSON bytes: new delivery,
    #    ingestion DUPLICATE (request hash identical).
    reformatted = json.dumps(document, separators=(", ", ": ")).encode("utf-8")
    duplicate = _deliver(publisher, consumer, node.topic, reformatted)
    assert duplicate.pk != first.pk and duplicate.state == "DUPLICATE"
    # 3) Same frame, different envelope metadata (rssi): metadata CONFLICT.
    document["rssi"] = -90
    changed = json.dumps(document, separators=(",", ":")).encode("utf-8")
    conflict = _deliver(publisher, consumer, node.topic, changed)
    assert conflict.pk not in (first.pk, duplicate.pk) and conflict.state == "CONFLICT"
    frames = ReceivedFrame.objects.filter(coordinator=node.coordinator, boot_counter=BOOT, seq=106)
    assert frames.count() == 1
    assert Measurement.objects.filter(frame=frames.get()).count() == 2
    EVIDENCE["E6"] = {
        "first": first.state,
        "redelivery_same_row": again.pk == first.pk,
        "redelivery_events": 1,
        "same_request_new_bytes": duplicate.state,
        "same_frame_other_metadata": conflict.state,
        "frames_for_seq": 1,
        "measurements_for_frame": 2,
    }


def test_05_e7_integrity_and_gateway_identity(node, consumer, publisher, admin, farm_id):
    accepted = _deliver(publisher, consumer, node.topic, _payload(_frame(node, seq=107, readings=DHT)))
    assert accepted.state == "ACCEPTED"
    # HMAC: frame signed with another key.
    bad_hmac = _deliver(
        publisher,
        consumer,
        node.topic,
        _payload(_frame(node, seq=108, readings=BME, key=bytes(32))),
    )
    assert bad_hmac.state == "REJECTED" and bad_hmac.result_code == "rejected_hmac"
    # Anti-replay: boot_counter older than the 4-boot window (7 seen -> 3 rejected).
    replay = _deliver(
        publisher, consumer, node.topic, _payload(_frame(node, seq=1, readings=BME, boot=3))
    )
    assert replay.state == "REJECTED" and replay.result_code == "rejected_replay"
    # Gateway identity only from the topic: a payload field is not accepted.
    forged_field = _deliver(
        publisher,
        consumer,
        node.topic,
        _payload(_frame(node, seq=109, readings=BME), extra={"gateway_uid": "GW-ZZZZZZZZZZ"}),
    )
    assert forged_field.state == "REJECTED"
    assert forged_field.result_code == "rejected_invalid_payload"
    # Valid frame on the topic of a gateway NOT authorized for this coordinator.
    other_gateway = iot.create_gateway(user=admin, farm_id=farm_id, name="Gateway 2")
    iot.issue_gateway_credential(user=admin, farm_id=farm_id, gateway_uid=other_gateway.uid)
    foreign_topic = f"agrotech/v1/gw/{other_gateway.uid}/up"
    unauthorized = _deliver(
        publisher, consumer, foreign_topic, _payload(_frame(node, seq=110, readings=BME))
    )
    assert unauthorized.state == "REJECTED"
    assert unauthorized.result_code == "rejected_authorization"
    for seq in (108, 109, 110):
        assert _frame_measurements(node, seq) == []
    assert _frame_measurements(node, 1, boot=3) == []
    EVIDENCE["E7"] = {
        "hmac": bad_hmac.result_code,
        "replay": replay.result_code,
        "payload_gateway_uid": forged_field.result_code,
        "unauthorized_topic_gateway": unauthorized.result_code,
    }
