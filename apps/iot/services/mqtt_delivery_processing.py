"""Process a stored MQTT delivery through the existing IoT ingestion service."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from django.core.exceptions import ImproperlyConfigured
from django.db import DEFAULT_DB_ALIAS, connections, router, transaction

from apps.farms.models import Farm
from apps.iot.broker_identity_config import (
    BrokerIdentityMode,
    require_operational_mode_from_env,
)
from apps.iot.db_guard import require_mariadb
from apps.iot.models import (
    BrokerIdentity,
    BrokerIdentityFingerprint,
    Coordinator,
    CoordinatorBootWindow,
    CoordinatorCredential,
    CoordinatorGateway,
    FrameConflict,
    Gateway,
    GatewayCredential,
    IotMqttDelivery,
    IotMqttDeliveryEvent,
    IotMqttDeliveryPause,
    IotMqttDeliveryPauseEvent,
    IotMqttDeliveryState,
    Measurement,
    ReceivedFrame,
    Sensor,
    SensorSource,
)
from apps.iot.mqtt.payload import PayloadError, TopicError, parse_payload, parse_topic
from apps.iot.services.broker_identity import record_identity_verdict
from apps.iot.services.ingestion import IngestionResult, IngestionStatus, ingest

_FINAL_STATES = frozenset(
    {
        IotMqttDeliveryState.ACCEPTED,
        IotMqttDeliveryState.DUPLICATE,
        IotMqttDeliveryState.REJECTED,
        IotMqttDeliveryState.CONFLICT,
        IotMqttDeliveryState.RESOLVED,
    }
)

_INGESTION_MODELS = (
    Farm,
    BrokerIdentity,
    BrokerIdentityFingerprint,
    Coordinator,
    CoordinatorBootWindow,
    CoordinatorCredential,
    CoordinatorGateway,
    FrameConflict,
    Gateway,
    GatewayCredential,
    IotMqttDelivery,
    IotMqttDeliveryEvent,
    IotMqttDeliveryPause,
    IotMqttDeliveryPauseEvent,
    Measurement,
    ReceivedFrame,
    Sensor,
    SensorSource,
)


class DeliveryProcessingOutcome(StrEnum):
    PROCESSED = "processed"
    ALREADY_FINAL = "already_final"
    NOT_READY = "not_ready"


@dataclass(frozen=True, slots=True)
class DeliveryProcessingResult:
    outcome: DeliveryProcessingOutcome
    delivery: IotMqttDelivery
    ingestion_result: IngestionResult | None = None


class DeliveryNotFoundError(LookupError):
    """The durable delivery identifier does not exist."""


class DeliveryPayloadMismatchError(ValueError):
    """The supplied plaintext does not match the durable transport fingerprint."""


class IngestionContractError(RuntimeError):
    """The existing ingestion service returned an unrecognized result."""


class LeaseLostError(RuntimeError):
    """The worker's fencing token is no longer current and live."""


def process_encrypted_mqtt_delivery(
    *, delivery_id: UUID, lease_owner: str | None = None, lease_token: UUID | None = None
) -> DeliveryProcessingResult:
    """Authenticate/decrypt stored ciphertext before normal delivery processing."""
    from apps.iot.services.mqtt_payload_crypto import decrypt_mqtt_delivery

    row = IotMqttDelivery.objects.filter(pk=delivery_id).first()
    if row is None:
        raise DeliveryNotFoundError("entrega MQTT no encontrada")
    plaintext = decrypt_mqtt_delivery(row)
    return process_mqtt_delivery(
        delivery_id=delivery_id,
        payload=plaintext,
        lease_owner=lease_owner,
        lease_token=lease_token,
    )


def process_mqtt_delivery(
    *, delivery_id: UUID, payload: bytes, lease_owner: str | None = None,
    lease_token: UUID | None = None,
) -> DeliveryProcessingResult:
    """Atomically process a fenced delivery and persist the ingestion result.

    The payload has already been authenticated/decrypted by the encrypted
    entrypoint or supplied by a trusted internal caller. The candidate
    coordinator FK is never used as authenticated identity; MQTT ACKs are out
    of scope.
    """
    if not isinstance(delivery_id, UUID):
        raise ValueError("identificador de entrega no válido")
    if type(payload) is not bytes:
        raise ValueError("payload descifrado debe ser bytes")

    _require_default_alias_for_ingestion()
    require_mariadb(DEFAULT_DB_ALIAS)

    if lease_owner is None and lease_token is None:
        from apps.iot.services.mqtt_delivery_leases import claim_mqtt_delivery
        lease_owner = f"inline:{delivery_id}"
        claimed = claim_mqtt_delivery(delivery_id=delivery_id, worker_id=lease_owner)
        if claimed is None:
            current = IotMqttDelivery.objects.filter(pk=delivery_id).first()
            if current is None:
                raise DeliveryNotFoundError("entrega MQTT no encontrada")
            if current.state in _FINAL_STATES:
                return DeliveryProcessingResult(DeliveryProcessingOutcome.ALREADY_FINAL, current)
            return DeliveryProcessingResult(DeliveryProcessingOutcome.NOT_READY, current)
        lease_token = claimed.lease_token
    if not isinstance(lease_owner, str) or not lease_owner or not isinstance(lease_token, UUID):
        raise ValueError("lease de entrega no válido")

    with transaction.atomic(using=DEFAULT_DB_ALIAS):
        connection = connections[DEFAULT_DB_ALIAS]
        if not connection.in_atomic_block:
            raise RuntimeError("la transacción exterior de entrega no está activa")

        delivery = (
            IotMqttDelivery.objects.select_for_update()
            .filter(pk=delivery_id)
            .first()
        )
        if delivery is None:
            raise DeliveryNotFoundError("entrega MQTT no encontrada")

        if delivery.state in _FINAL_STATES:
            return DeliveryProcessingResult(
                outcome=DeliveryProcessingOutcome.ALREADY_FINAL,
                delivery=delivery,
            )
        from apps.iot.services.mqtt_delivery_leases import verify_mqtt_delivery_lease
        if not verify_mqtt_delivery_lease(delivery, worker_id=lease_owner, lease_token=lease_token):
            return DeliveryProcessingResult(
                outcome=DeliveryProcessingOutcome.NOT_READY,
                delivery=delivery,
            )

        from apps.iot.services.mqtt_payload_crypto import decrypt_mqtt_delivery
        authenticated_payload = decrypt_mqtt_delivery(delivery)
        if authenticated_payload != payload:
            raise DeliveryPayloadMismatchError(
                "el payload no coincide con el sobre cifrado autenticado"
            )

        if (
            len(payload) != delivery.payload_size
            or hashlib.sha256(payload).digest() != bytes(delivery.payload_sha256)
        ):
            raise DeliveryPayloadMismatchError(
                "el payload descifrado no coincide con la huella durable"
            )

        # S1: the server receipt time is the durable consumer receipt, not the
        # (possibly much later, after retries) processing time.
        result, reason_code = _invoke_ingestion(
            topic=delivery.topic,
            payload=payload,
            server_received_at=delivery.received_at,
        )
        final_state = _state_for_ingestion_result(result)

        if not verify_mqtt_delivery_lease(delivery, worker_id=lease_owner, lease_token=lease_token):
            raise LeaseLostError("el lease de la entrega venció durante el procesamiento")

        delivery.state = final_state
        if result.authenticated_coordinator_id is not None:
            delivery.authenticated_coordinator_id = result.authenticated_coordinator_id
        delivery.result_category = final_state.lower()
        delivery.result_code = result.status.value
        delivery.reason_code = reason_code
        delivery.save(
            update_fields=[
                "state",
                "result_category",
                "result_code",
                "reason_code",
                "authenticated_coordinator",
                "updated_at",
            ]
        )
        delivery.lease_owner = None
        delivery.lease_token = None
        delivery.lease_expires_at = None
        delivery.save(
            update_fields=["lease_owner", "lease_token", "lease_expires_at", "updated_at"]
        )
        IotMqttDeliveryEvent.objects.create(
            delivery=delivery,
            event_type="PROCESSING_COMPLETED",
            from_state=IotMqttDeliveryState.PROCESSING,
            to_state=final_state,
            result_code=result.status.value,
            worker_id=lease_owner,
            lease_token=lease_token,
            correlation_id=delivery.id,
            safe_detail="Resultado de ingesta durable registrado",
        )
        # BUILD-FLEPSI-02: veredicto de identidad sobre la fila BLOQUEADA
        # (``delivery``), solo informativo en record; no cambia ``final_state``.
        if _broker_identity_records():
            record_identity_verdict(delivery, worker_id=lease_owner)

        return DeliveryProcessingResult(
            outcome=DeliveryProcessingOutcome.PROCESSED,
            delivery=delivery,
            ingestion_result=result,
        )


def _invoke_ingestion(
    *, topic: str, payload: bytes, server_received_at: datetime
) -> tuple[IngestionResult, str | None]:
    """Reuse MQTT envelope parsing and delegate authenticated processing."""
    try:
        gateway_uid = parse_topic(topic)
    except TopicError:
        return IngestionResult(IngestionStatus.REJECTED_INVALID_PAYLOAD), "invalid_topic"

    try:
        validated_payload = parse_payload(payload)
    except PayloadError as error:
        return (
            IngestionResult(IngestionStatus.REJECTED_INVALID_PAYLOAD),
            error.reason.value,
        )

    # The topic supplies gateway identity. Coordinator identity is resolved
    # from the frame and authenticated only inside ingest(); the delivery's
    # coordinator candidate is deliberately not consulted.
    result = ingest(
        gateway_uid=gateway_uid,
        payload=validated_payload,
        server_received_at=server_received_at,
    )
    if type(result) is not IngestionResult or type(result.status) is not IngestionStatus:
        raise IngestionContractError("ingesta devolvió un resultado no reconocido")
    return result, result.status.value


def _state_for_ingestion_result(result: IngestionResult) -> IotMqttDeliveryState:
    if result.status is IngestionStatus.ACCEPTED:
        return IotMqttDeliveryState.ACCEPTED
    if result.status is IngestionStatus.DUPLICATE:
        return IotMqttDeliveryState.DUPLICATE
    if result.status is IngestionStatus.CONFLICT:
        return IotMqttDeliveryState.CONFLICT
    if result.status in {
        IngestionStatus.REJECTED_INVALID_PAYLOAD,
        IngestionStatus.REJECTED_GATEWAY,
        IngestionStatus.REJECTED_AUTHORIZATION,
        IngestionStatus.REJECTED_CREDENTIAL,
        IngestionStatus.REJECTED_HMAC,
        IngestionStatus.REJECTED_GENERATION,
        IngestionStatus.REJECTED_REPLAY,
        IngestionStatus.REJECTED_SENSOR_MAPPING,
    }:
        return IotMqttDeliveryState.REJECTED
    raise IngestionContractError("ingesta devolvió una categoría no reconocida")


def _broker_identity_records() -> bool:
    """True en record; ``enforce`` o un modo inválido es un error explícito.

    BUILD-FLEPSI-02H: el worker ya lo comprueba antes de cada lote, así que
    aquí solo se llega si el entorno cambiara a mitad de lote.
    """
    return require_operational_mode_from_env() is BrokerIdentityMode.RECORD


def _require_default_alias_for_ingestion() -> None:
    """Ensure the outer transaction covers every ORM model used by ingestion."""
    for model in _INGESTION_MODELS:
        read_alias = router.db_for_read(model)
        write_alias = router.db_for_write(model)
        if read_alias not in (None, DEFAULT_DB_ALIAS) or write_alias not in (
            None,
            DEFAULT_DB_ALIAS,
        ):
            raise ImproperlyConfigured(
                "la ingesta IoT y la bandeja durable deben usar el alias default"
            )


__all__ = [
    "DeliveryNotFoundError",
    "DeliveryPayloadMismatchError",
    "DeliveryProcessingOutcome",
    "DeliveryProcessingResult",
    "IngestionContractError",
    "LeaseLostError",
    "process_encrypted_mqtt_delivery",
    "process_mqtt_delivery",
]
