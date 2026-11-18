# ruff: noqa: S101, S105, S106
"""End-to-end MQTT -> durable receipt -> worker against an isolated broker.

The test is skipped unless the disposable test broker descriptor exists. The
descriptor and broker are local, ephemeral artifacts; no production secret is
read. Run with the disposable MariaDB orchestrator and the IoT settings module.
"""

from __future__ import annotations

import base64
import json
import secrets
import socket
import ssl
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import paho.mqtt.client as mqtt
import pytest
from django.db import connections

import apps.iot.services as iot
from apps.iot.models import (
    Coordinator,
    CoordinatorCredential,
    Gateway,
    IotMqttDelivery,
    IotMqttDeliveryEvent,
    IotMqttDeliveryState,
    Measurement,
)
from apps.iot.mqtt.consumer import MqttConsumer, MqttSettings
from apps.iot.services.mqtt_delivery_worker import run_mqtt_delivery_worker_once
from apps.iot.tests import lora_vectors
from apps.iot.tests.conftest import make_source_uid

pytestmark = pytest.mark.django_db(transaction=True)

BROKER_DESCRIPTOR = (
    Path(tempfile.gettempdir()) / "opencode" / "mqtt-broker" / "pytest.json"
)
CONNECT_TIMEOUT = 15.0


@dataclass(frozen=True)
class World:
    gateway: Gateway
    coordinator: Coordinator
    credential: CoordinatorCredential
    key: bytes


@pytest.fixture(autouse=True)
def key_settings(settings):
    settings.COORDINATOR_KEY_ENCRYPTION_KEY = base64.b64encode(
        secrets.token_bytes(32)
    ).decode("ascii")
    settings.COORDINATOR_KEY_ENCRYPTION_KEY_VERSION = "1"
    settings.COORDINATOR_KEY_ENCRYPTION_KEY_PREVIOUS = ""
    settings.GATEWAY_CREDENTIAL_HMAC_KEY = secrets.token_urlsafe(48)
    settings.SECRET_KEY = "test-" + secrets.token_urlsafe(48)


@pytest.fixture
def world(admin, farm_id) -> World:
    gateway = iot.create_gateway(user=admin, farm_id=farm_id, name="Gateway")
    coordinator = iot.create_coordinator(
        user=admin, farm_id=farm_id, name="Coordinador"
    )
    iot.authorize_gateway(
        user=admin,
        farm_id=farm_id,
        coordinator_uid=coordinator.uid,
        gateway_uid=gateway.uid,
    )
    iot.issue_gateway_credential(user=admin, farm_id=farm_id, gateway_uid=gateway.uid)
    issued = iot.issue_coordinator_credential(
        user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid
    )
    source_uid = make_source_uid(
        user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid
    )
    iot.create_sensor(
        user=admin,
        farm_id=farm_id,
        coordinator_uid=coordinator.uid,
        source_uid=source_uid,
        channel=1,
        metric="temperature",
    )
    iot.create_sensor(
        user=admin,
        farm_id=farm_id,
        coordinator_uid=coordinator.uid,
        source_uid=source_uid,
        channel=2,
        metric="relative_humidity",
    )
    return World(
        gateway=gateway,
        coordinator=coordinator,
        credential=issued.credential,
        key=issued.key,
    )


def _broker_config() -> dict[str, object]:
    if not BROKER_DESCRIPTOR.is_file():
        pytest.skip("broker MQTT de pruebas no preparado")
    config = json.loads(BROKER_DESCRIPTOR.read_text(encoding="utf-8"))
    try:
        with socket.create_connection(
            (str(config["host"]), int(config["port"])), timeout=3
        ):
            pass
    except OSError:
        pytest.skip("broker MQTT de pruebas no alcanzable")
    return config


def _frame(world: World, *, seq: int) -> bytes:
    return lora_vectors.frame(
        uid=world.coordinator.uid,
        key=world.key,
        key_version=world.credential.key_version,
        boot_counter=7,
        seq=seq,
        readings=lora_vectors.READINGS,
    )


def _raw_payload(frame: bytes) -> bytes:
    document = {
        "v": 1,
        "frame": base64.b64encode(frame).decode("ascii"),
        "lora_rx_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace(
            "+00:00", "Z"
        ),
        "clock_synced": True,
        "buffered": False,
        "rssi": -87,
        "snr": 7.5,
    }
    return json.dumps(document, separators=(",", ":")).encode("utf-8")


def _publisher(config: dict[str, object]) -> mqtt.Client:
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"pytest-gateway-{uuid4().hex[:10]}",
        clean_session=True,
    )
    client.username_pw_set(
        str(config["publisher_user"]), str(config["publisher_password"])
    )
    client.tls_set(
        ca_certs=str(config["ca_certs"]),
        cert_reqs=ssl.CERT_REQUIRED,
        tls_version=ssl.PROTOCOL_TLS_CLIENT,
    )
    client.connect(str(config["host"]), int(config["port"]), keepalive=15)
    client.loop_start()
    return client


def _publish(
    client: mqtt.Client, config: dict[str, object], topic: str, payload: bytes
) -> None:
    info = client.publish(topic, payload, qos=1)
    info.wait_for_publish(CONNECT_TIMEOUT)
    assert info.is_published()


def _start_consumer(
    config: dict[str, object], *, client_id: str
) -> tuple[MqttConsumer, threading.Thread, threading.Event]:
    settings = MqttSettings(
        host=str(config["host"]),
        port=int(config["port"]),
        client_id=client_id,
        username=str(config["consumer_user"]),
        password=str(config["consumer_password"]),
        tls=True,
        ca_certs=str(config["ca_certs"]),
        keepalive=15,
        reconnect_min_delay=1,
        reconnect_max_delay=5,
    )
    consumer = MqttConsumer(settings)
    subscribed = threading.Event()
    consumer._client.on_subscribe = lambda *args, **kwargs: subscribed.set()
    thread = threading.Thread(target=consumer.run, daemon=True)
    thread.start()
    assert subscribed.wait(CONNECT_TIMEOUT), "consumidor no se suscribió"
    return consumer, thread, subscribed


def _stop_consumer(consumer: MqttConsumer, thread: threading.Thread) -> None:
    consumer.stop()
    thread.join(CONNECT_TIMEOUT)


def _wait_for(predicate, *, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        connections.close_all()
        if predicate():
            return True
        time.sleep(0.1)
    return False


def test_end_to_end_durable_reception_and_worker(world: World) -> None:
    config = _broker_config()
    topic = f"{config['topic_prefix']}/{world.gateway.uid}/up"
    client_id = f"pytest-consumer-{uuid4().hex[:10]}"
    publisher = _publisher(config)
    consumer = thread = None
    try:
        consumer, thread, _ = _start_consumer(config, client_id=client_id)

        first = _raw_payload(_frame(world, seq=120))
        _publish(publisher, config, topic, first)
        assert _wait_for(
            lambda: IotMqttDelivery.objects.filter(topic=topic).count() == 1
        ), "no se persistió la recepción durable"
        delivery = IotMqttDelivery.objects.get(topic=topic)
        assert delivery.state == IotMqttDeliveryState.RECEIVED
        assert delivery.payload_size == len(first)
        assert bytes(delivery.payload_ciphertext) != first
        assert first not in bytes(delivery.payload_ciphertext)
        assert delivery.payload_key_version == "1"
        assert IotMqttDeliveryEvent.objects.filter(
            delivery=delivery, event_type="RECEIVED"
        ).exists()

        _publish(publisher, config, topic, first)
        assert _wait_for(
            lambda: IotMqttDeliveryEvent.objects.filter(
                delivery=delivery, event_type="REDELIVERY"
            ).exists()
        ), "la redelivery QoS1 no se registró"
        assert IotMqttDelivery.objects.filter(topic=topic).count() == 1

        IotMqttDelivery.objects.filter(pk=delivery.pk).update(
            state=IotMqttDeliveryState.PROCESSING,
            lease_owner="crashed-worker",
            lease_token=uuid4(),
            lease_expires_at=datetime.now(UTC) - timedelta(minutes=5),
        )
        batch = run_mqtt_delivery_worker_once(worker_id="pytest-worker")
        assert batch.processed == 1
        delivery.refresh_from_db()
        assert delivery.state == IotMqttDeliveryState.ACCEPTED
        assert delivery.lease_owner is None
        assert delivery.lease_token is None
        assert delivery.authenticated_coordinator_id == world.coordinator.pk
        assert Measurement.objects.count() == 2
        assert IotMqttDeliveryEvent.objects.filter(
            delivery=delivery, event_type="PROCESSING_COMPLETED"
        ).exists()

        _stop_consumer(consumer, thread)
        consumer = thread = None

        second = _raw_payload(_frame(world, seq=121))
        _publish(publisher, config, topic, second)
        consumer, thread, _ = _start_consumer(config, client_id=client_id)
        assert _wait_for(
            lambda: IotMqttDelivery.objects.filter(topic=topic).count() == 2
        ), "el mensaje en cola no se recibió tras reconectar"
        second_delivery = (
            IotMqttDelivery.objects.filter(topic=topic)
            .exclude(pk=delivery.pk)
            .get()
        )
        assert second_delivery.payload_size == len(second)
        batch = run_mqtt_delivery_worker_once(worker_id="pytest-worker")
        assert batch.processed == 1
        second_delivery.refresh_from_db()
        assert second_delivery.state == IotMqttDeliveryState.ACCEPTED
        assert Measurement.objects.count() == 4
    finally:
        if consumer is not None and thread is not None:
            _stop_consumer(consumer, thread)
        publisher.loop_stop()
        publisher.disconnect()
