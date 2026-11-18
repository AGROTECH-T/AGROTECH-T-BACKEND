# ruff: noqa: S101, S105
"""Apoyo compartido para las pruebas de la bandeja MQTT durable (BUILD-01D-A).

No es un módulo de pruebas: solo fixtures y constructores reutilizados por
``test_mqtt_delivery_services.py`` y ``test_mqtt_delivery_concurrency.py``.
Las claves se generan aleatoriamente por prueba; no se lee ningún secreto real.
"""

from __future__ import annotations

import base64
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

import apps.iot.services as iot
from apps.iot.models import Coordinator, CoordinatorCredential, Gateway, IotMqttDelivery
from apps.iot.mqtt.payload import MAX_MESSAGE_BYTES
from apps.iot.services.mqtt_payload_crypto import register_encrypted_mqtt_delivery
from apps.iot.tests import lora_vectors
from apps.iot.tests.conftest import make_source_uid

LANE_TOPIC = "agrotech/v1/gw/GW-7K3M9QXT2A/up"


@dataclass(frozen=True)
class World:
    gateway: Gateway
    coordinator: Coordinator
    credential: CoordinatorCredential
    key: bytes

    @property
    def topic(self) -> str:
        return f"agrotech/v1/gw/{self.gateway.uid}/up"


@pytest.fixture
def mqtt_key_settings(settings):
    """Claves efímeras de prueba (mismo patrón que test_mqtt_durable_e2e)."""
    settings.COORDINATOR_KEY_ENCRYPTION_KEY = base64.b64encode(
        secrets.token_bytes(32)
    ).decode("ascii")
    settings.COORDINATOR_KEY_ENCRYPTION_KEY_VERSION = "1"
    settings.COORDINATOR_KEY_ENCRYPTION_KEY_PREVIOUS = ""
    settings.GATEWAY_CREDENTIAL_HMAC_KEY = secrets.token_urlsafe(48)
    settings.SECRET_KEY = "test-" + secrets.token_urlsafe(48)


@pytest.fixture
def world(mqtt_key_settings, admin, farm_id) -> World:
    """Gateway, coordinador autorizado con credencial y dos sensores."""
    gateway = iot.create_gateway(user=admin, farm_id=farm_id, name="Gateway")
    coordinator = iot.create_coordinator(user=admin, farm_id=farm_id, name="Coordinador")
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
    for channel, metric in ((1, "temperature"), (2, "relative_humidity")):
        iot.create_sensor(
            user=admin,
            farm_id=farm_id,
            coordinator_uid=coordinator.uid,
            source_uid=source_uid,
            channel=channel,
            metric=metric,
        )
    return World(
        gateway=gateway,
        coordinator=coordinator,
        credential=issued.credential,
        key=issued.key,
    )


@pytest.fixture
def lanes(mqtt_key_settings, admin, farm_id) -> tuple[Coordinator, Coordinator]:
    """Dos coordinadores independientes para probar carriles FIFO."""
    first = iot.create_coordinator(user=admin, farm_id=farm_id, name="Carril A")
    second = iot.create_coordinator(user=admin, farm_id=farm_id, name="Carril B")
    return first, second


def frame_for(world: World, *, seq: int, key: bytes | None = None) -> bytes:
    return lora_vectors.frame(
        uid=world.coordinator.uid,
        key=world.key if key is None else key,
        key_version=world.credential.key_version,
        boot_counter=7,
        seq=seq,
        readings=lora_vectors.READINGS,
    )


def raw_payload(frame: bytes) -> bytes:
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


def register(
    payload: bytes,
    *,
    topic: str = LANE_TOPIC,
    coordinator_id: int | None = None,
):
    """Recepción durable real: cifrado AES-GCM + inserción en MariaDB."""
    return register_encrypted_mqtt_delivery(
        topic=topic,
        payload=payload,
        qos=1,
        max_payload_bytes=MAX_MESSAGE_BYTES,
        coordinator_id=coordinator_id,
    )


def register_lane(
    marker: str,
    *,
    coordinator_id: int | None,
    received_offset_s: int,
) -> IotMqttDelivery:
    """Entrega opaca con orden de recepción explícito para pruebas de carril."""
    result = register(
        json.dumps({"marker": marker}).encode("utf-8"),
        coordinator_id=coordinator_id,
    )
    assert result.delivery is not None
    base = datetime(2026, 1, 1, tzinfo=UTC)
    IotMqttDelivery.objects.filter(pk=result.delivery.pk).update(
        received_at=base + timedelta(seconds=received_offset_s)
    )
    result.delivery.refresh_from_db()
    return result.delivery
