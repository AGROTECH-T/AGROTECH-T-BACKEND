"""Clasificación del contrato de entrega MQTT (B2.1-02)."""

from __future__ import annotations

from enum import StrEnum

from django.db import InterfaceError, OperationalError

from apps.iot.services.ingestion import IngestionResult, IngestionStatus


class DeliveryClassification(StrEnum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    CONFLICT = "conflict"
    REJECTED_INVALID_PAYLOAD = "rejected_invalid_payload"
    REJECTED_GATEWAY = "rejected_gateway"
    REJECTED_AUTHORIZATION = "rejected_authorization"
    REJECTED_CREDENTIAL = "rejected_credential"
    REJECTED_HMAC = "rejected_hmac"
    REJECTED_GENERATION = "rejected_generation"
    REJECTED_REPLAY = "rejected_replay"
    REJECTED_SENSOR_MAPPING = "rejected_sensor_mapping"
    TRANSIENT_ERROR = "transient_error"
    OPERATIONAL_ERROR = "operational_error"


_RESULT_CLASSIFICATION = {
    IngestionStatus.ACCEPTED: DeliveryClassification.ACCEPTED,
    IngestionStatus.DUPLICATE: DeliveryClassification.DUPLICATE,
    IngestionStatus.CONFLICT: DeliveryClassification.CONFLICT,
    IngestionStatus.REJECTED_INVALID_PAYLOAD: DeliveryClassification.REJECTED_INVALID_PAYLOAD,
    IngestionStatus.REJECTED_REPLAY: DeliveryClassification.REJECTED_REPLAY,
    IngestionStatus.REJECTED_SENSOR_MAPPING: DeliveryClassification.REJECTED_SENSOR_MAPPING,
    IngestionStatus.REJECTED_GATEWAY: DeliveryClassification.REJECTED_GATEWAY,
    IngestionStatus.REJECTED_AUTHORIZATION: DeliveryClassification.REJECTED_AUTHORIZATION,
    IngestionStatus.REJECTED_CREDENTIAL: DeliveryClassification.REJECTED_CREDENTIAL,
    IngestionStatus.REJECTED_HMAC: DeliveryClassification.REJECTED_HMAC,
    IngestionStatus.REJECTED_GENERATION: DeliveryClassification.REJECTED_GENERATION,
}


def classify_result(result: IngestionResult) -> DeliveryClassification:
    """Mapea cada resultado tipado de ingesta al contrato de entrega."""
    if type(result) is not IngestionResult:
        return DeliveryClassification.OPERATIONAL_ERROR
    try:
        status = result.status
        if type(status) is not IngestionStatus:
            return DeliveryClassification.OPERATIONAL_ERROR
        return _RESULT_CLASSIFICATION[status]
    except Exception:
        return DeliveryClassification.OPERATIONAL_ERROR


def classify_exception(error: BaseException) -> DeliveryClassification:
    """Solo errores de conectividad/operación DB conocidos son recuperables."""
    if isinstance(error, (OperationalError, InterfaceError, TimeoutError, ConnectionError)):
        return DeliveryClassification.TRANSIENT_ERROR
    return DeliveryClassification.OPERATIONAL_ERROR


def ack_is_allowed(classification: DeliveryClassification) -> bool:
    """En esta fase solo ingestas confirmadas y duplicados aceptados pueden ACK."""
    return classification in {
        DeliveryClassification.ACCEPTED,
        DeliveryClassification.DUPLICATE,
    }


__all__ = [
    "DeliveryClassification",
    "ack_is_allowed",
    "classify_exception",
    "classify_result",
]
