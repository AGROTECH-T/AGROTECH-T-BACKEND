"""Validación estricta del topic y del payload MQTT (IoT-04).

Separa el transporte de la lógica de negocio: aquí no se toca la base de datos
ni se autentica la trama. El resultado es un ``ValidatedPayload`` con la trama
en bytes y los metadatos del gateway ya normalizados.

El payload es un objeto JSON con exactamente estos campos:

``v`` (int 1), ``frame`` (Base64 canónico), ``lora_rx_at`` (ISO-8601 con zona o
null), ``clock_synced`` (bool), ``buffered`` (bool) y, opcionalmente,
``rssi`` (int dBm) y ``snr`` (número dB).

Límites de RSSI/SNR: no hay un contrato documentado; se usan cotas
conservadoras para el SX1278 (RSSI -164..0 dBm, SNR -20..20 dB) y se rechazan
valores fuera de rango, no finitos o de tipo booleano. El límite de tamaño del
mensaje se deriva del máximo de la trama v1.
"""

from __future__ import annotations

import base64
import binascii
import json
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import StrEnum

from apps.iot import lora_frame
from apps.iot.identifiers import IdentifierKind, is_valid_identifier

TOPIC_TEMPLATE = "agrotech/v1/gw/{gateway_uid}/up"
TOPIC_SEGMENTS = 5
TOPIC_PREFIX = "agrotech"
TOPIC_VERSION = "v1"
TOPIC_DIRECTION = "up"

REQUIRED_FIELDS = frozenset({"v", "frame", "lora_rx_at", "clock_synced", "buffered"})
OPTIONAL_FIELDS = frozenset({"rssi", "snr"})
ALLOWED_FIELDS = REQUIRED_FIELDS | OPTIONAL_FIELDS

FRAME_VERSION = lora_frame.FRAME_VERSION_V1
MAX_FRAME_BASE64_CHARS = 4 * ((lora_frame.MAX_FRAME_BYTES_V1 + 2) // 3)
ENVELOPE_BUDGET_BYTES = 256
MAX_MESSAGE_BYTES = MAX_FRAME_BASE64_CHARS + ENVELOPE_BUDGET_BYTES

MIN_RSSI_DBM = -164
MAX_RSSI_DBM = 0
MIN_SNR_DB = Decimal("-20.00")
MAX_SNR_DB = Decimal("20.00")
SNR_QUANTUM = Decimal("0.01")

INVALID_PAYLOAD_MESSAGE = "payload MQTT no válido"
INVALID_TOPIC_MESSAGE = "topic MQTT no válido"


class PayloadRejection(StrEnum):
    """Motivo interno de rechazo; nunca se expone al gateway."""

    INVALID_TYPE = "invalid_type"
    TOO_LARGE = "too_large"
    INVALID_UTF8 = "invalid_utf8"
    INVALID_JSON = "invalid_json"
    NOT_OBJECT = "not_object"
    DUPLICATE_FIELD = "duplicate_field"
    UNKNOWN_FIELD = "unknown_field"
    MISSING_FIELD = "missing_field"
    INVALID_VERSION = "invalid_version"
    INVALID_FRAME = "invalid_frame"
    INVALID_TIMESTAMP = "invalid_timestamp"
    INVALID_BOOLEAN = "invalid_boolean"
    INVALID_RSSI = "invalid_rssi"
    INVALID_SNR = "invalid_snr"


class PayloadError(ValueError):
    """El mensaje no cumple el contrato; el llamador puede confirmarlo."""

    def __init__(self, reason: PayloadRejection) -> None:
        super().__init__(INVALID_PAYLOAD_MESSAGE)
        self.reason = reason


class TopicError(ValueError):
    """El topic no cumple el contrato; el llamador puede confirmarlo."""

    def __init__(self) -> None:
        super().__init__(INVALID_TOPIC_MESSAGE)


@dataclass(frozen=True, slots=True)
class ValidatedPayload:
    """Payload validado; ``frame`` nunca aparece en ``repr``."""

    frame: bytes = field(repr=False)
    lora_rx_at: datetime | None
    clock_synced: bool
    buffered: bool
    rssi: int | None
    snr: Decimal | None


def parse_topic(topic: object) -> str:
    """Devuelve el ``gateway_uid`` canónico del topic o lanza ``TopicError``."""
    if not isinstance(topic, str) or not topic.isascii():
        raise TopicError()
    parts = topic.split("/")
    if len(parts) != TOPIC_SEGMENTS:
        raise TopicError()
    prefix, version, channel, gateway_uid, direction = parts
    if (prefix, version, channel, direction) != (
        TOPIC_PREFIX,
        TOPIC_VERSION,
        "gw",
        TOPIC_DIRECTION,
    ):
        raise TopicError()
    if not is_valid_identifier(gateway_uid, IdentifierKind.GATEWAY):
        raise TopicError()
    return gateway_uid


def parse_payload(raw: object) -> ValidatedPayload:
    """Valida el mensaje completo o lanza ``PayloadError`` sin revelar datos."""
    if not isinstance(raw, (bytes, bytearray)):
        raise PayloadError(PayloadRejection.INVALID_TYPE)
    data = bytes(raw)
    if not data or len(data) > MAX_MESSAGE_BYTES:
        raise PayloadError(PayloadRejection.TOO_LARGE)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise PayloadError(PayloadRejection.INVALID_UTF8) from None
    try:
        document = json.loads(
            text,
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_json_constant,
        )
    except PayloadError:
        raise
    except (json.JSONDecodeError, ValueError):
        raise PayloadError(PayloadRejection.INVALID_JSON) from None
    if not isinstance(document, dict):
        raise PayloadError(PayloadRejection.NOT_OBJECT)

    fields = frozenset(document)
    if fields - ALLOWED_FIELDS:
        raise PayloadError(PayloadRejection.UNKNOWN_FIELD)
    if REQUIRED_FIELDS - fields:
        raise PayloadError(PayloadRejection.MISSING_FIELD)

    version = document["v"]
    if type(version) is not int or version != FRAME_VERSION:
        raise PayloadError(PayloadRejection.INVALID_VERSION)

    frame = _decode_frame(document["frame"])
    lora_rx_at = _parse_timestamp(document["lora_rx_at"])
    clock_synced = _parse_bool(document["clock_synced"])
    buffered = _parse_bool(document["buffered"])
    rssi = _parse_rssi(document.get("rssi"))
    snr = _parse_snr(document.get("snr"))

    return ValidatedPayload(
        frame=frame,
        lora_rx_at=lora_rx_at,
        clock_synced=clock_synced,
        buffered=buffered,
        rssi=rssi,
        snr=snr,
    )


def _object_without_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise PayloadError(PayloadRejection.DUPLICATE_FIELD)
        result[key] = value
    return result


def _reject_json_constant(_value: str) -> object:
    raise PayloadError(PayloadRejection.INVALID_JSON)


def _decode_frame(value: object) -> bytes:
    if not isinstance(value, str) or not value.isascii():
        raise PayloadError(PayloadRejection.INVALID_FRAME)
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise PayloadError(PayloadRejection.INVALID_FRAME) from None
    if base64.b64encode(decoded).decode("ascii") != value:
        raise PayloadError(PayloadRejection.INVALID_FRAME)
    if not lora_frame.MIN_FRAME_BYTES_V1 <= len(decoded) <= lora_frame.MAX_FRAME_BYTES_V1:
        raise PayloadError(PayloadRejection.INVALID_FRAME)
    return decoded


def _parse_timestamp(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise PayloadError(PayloadRejection.INVALID_TIMESTAMP)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise PayloadError(PayloadRejection.INVALID_TIMESTAMP) from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PayloadError(PayloadRejection.INVALID_TIMESTAMP)
    return parsed.astimezone(UTC)


def _parse_bool(value: object) -> bool:
    if type(value) is not bool:
        raise PayloadError(PayloadRejection.INVALID_BOOLEAN)
    return value


def _parse_rssi(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int or not MIN_RSSI_DBM <= value <= MAX_RSSI_DBM:
        raise PayloadError(PayloadRejection.INVALID_RSSI)
    return value


def _parse_snr(value: object) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PayloadError(PayloadRejection.INVALID_SNR)
    if not math.isfinite(value):
        raise PayloadError(PayloadRejection.INVALID_SNR)
    try:
        snr = Decimal(str(value)).quantize(SNR_QUANTUM, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        raise PayloadError(PayloadRejection.INVALID_SNR) from None
    if not MIN_SNR_DB <= snr <= MAX_SNR_DB:
        raise PayloadError(PayloadRejection.INVALID_SNR)
    return snr


__all__ = [
    "ALLOWED_FIELDS",
    "INVALID_PAYLOAD_MESSAGE",
    "INVALID_TOPIC_MESSAGE",
    "MAX_MESSAGE_BYTES",
    "OPTIONAL_FIELDS",
    "PayloadError",
    "PayloadRejection",
    "REQUIRED_FIELDS",
    "TOPIC_TEMPLATE",
    "TopicError",
    "ValidatedPayload",
    "parse_payload",
    "parse_topic",
]
