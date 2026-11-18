"""Parser binario de la trama LoRa v1 del coordinador (IoT-04.2, DEC-13 §14).

Propósito: convertir los bytes exactos recibidos por LoRa (ya decodificados de
Base64) en una ``LoraFrameV1`` inmutable, o rechazarlos con ``LoraFrameError``.

Contrato v1 (``docs/modules/iot-contrato-lora.md`` §2.7), todo big-endian:

- Cabecera de 25 bytes: ``frame_version`` (0, uint8), ``coordinator_uid`` (1,
  13 bytes ASCII), ``key_version`` (14, uint16), ``boot_counter`` (16, uint32),
  ``seq`` (20, uint32) y ``reading_count`` (24, uint8).
- ``reading_count`` bloques de 5 bytes desde el offset 25: ``channel`` (uint16),
  ``metric_code`` (uint8) y ``value`` (int16 o uint16 según la métrica).
- Etiqueta HMAC-SHA256 completa de 32 bytes en ``25 + 5 × reading_count``.
- Longitud exacta: ``25 + 5 × reading_count + 32``.

Responsabilidad: solo la estructura de la trama. No verifica el HMAC, no
consulta la base, no aplica rangos físicos ni decide qué hacer con un valor
centinela (solo lo identifica). Es puro: sin Django, ORM, configuración, red ni
archivos.

M-02: ``signed_bytes`` y ``tag`` se cortan una sola vez de la trama original y
todos los campos se decodifican de esos mismos ``signed_bytes``. Ningún campo es
de confianza hasta que el HMAC se verifica sobre ellos.

Seguridad: ni los mensajes de error ni los ``repr`` incluyen bytes de la trama,
el tag ni el uid recibido.
"""
from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType

from apps.iot.identifiers import IdentifierKind, is_valid_identifier

FRAME_VERSION_V1 = 1
HEADER_BYTES = 25
READING_BYTES = 5
TAG_BYTES = 32
UID_BYTES = 13
MIN_READINGS = 1
# Máximo provisional (P-06): no es una capacidad física validada del SX1278.
MAX_READINGS_V1 = 16
MIN_CHANNEL = 1
MAX_CHANNEL = 65535
MIN_KEY_VERSION = 1
MIN_FRAME_BYTES_V1 = HEADER_BYTES + READING_BYTES * MIN_READINGS + TAG_BYTES  # 62
MAX_FRAME_BYTES_V1 = HEADER_BYTES + READING_BYTES * MAX_READINGS_V1 + TAG_BYTES  # 137

# Contexto de dominio del HMAC (D-10): no se transmite; se antepone a signed_bytes.
LORA_FRAME_HMAC_CONTEXT = b"agrotech:lora-frame:v1"

# Códigos de métrica en la trama. Los valores coinciden con
# ``apps.iot.catalog.MetricCode`` (comprobado en las pruebas); se declaran como
# texto para que el parser no dependa de Django.
METRIC_CODE_TEMPERATURE = 1
METRIC_CODE_RELATIVE_HUMIDITY = 2
METRIC_TEMPERATURE = "temperature"
METRIC_RELATIVE_HUMIDITY = "relative_humidity"

# Valores reservados de "lectura inválida" (D-09). Solo se identifican.
TEMPERATURE_INVALID_MARKER = -32768
HUMIDITY_INVALID_MARKER = 65535

if TAG_BYTES != hashlib.sha256().digest_size:  # pragma: no cover - invariante
    raise RuntimeError("TAG_BYTES debe coincidir con la salida de SHA-256")
if len(LORA_FRAME_HMAC_CONTEXT) != 22:  # pragma: no cover - invariante
    raise RuntimeError("el contexto HMAC de la trama v1 debe medir 22 bytes")

_HEADER = struct.Struct(">B13sHIIB")
_READING_HEAD = struct.Struct(">HB")
_INT16 = struct.Struct(">h")
_UINT16 = struct.Struct(">H")

if _HEADER.size != HEADER_BYTES:  # pragma: no cover - invariante
    raise RuntimeError("la cabecera v1 debe medir 25 bytes")
if _READING_HEAD.size + _INT16.size != READING_BYTES:  # pragma: no cover - invariante
    raise RuntimeError("el bloque de lectura v1 debe medir 5 bytes")


@dataclass(frozen=True, slots=True)
class _WireMetric:
    metric: str
    value_format: struct.Struct
    invalid_marker: int


_WIRE_METRICS = MappingProxyType(
    {
        METRIC_CODE_TEMPERATURE: _WireMetric(
            METRIC_TEMPERATURE, _INT16, TEMPERATURE_INVALID_MARKER
        ),
        METRIC_CODE_RELATIVE_HUMIDITY: _WireMetric(
            METRIC_RELATIVE_HUMIDITY, _UINT16, HUMIDITY_INVALID_MARKER
        ),
    }
)
_INVALID_MARKERS = MappingProxyType(
    {wire.metric: wire.invalid_marker for wire in _WIRE_METRICS.values()}
)

INVALID_FRAME_MESSAGE = "trama LoRa no válida"


class LoraFrameRejection(StrEnum):
    """Motivo interno de rechazo estructural (para métricas y cuarentena)."""

    INVALID_TYPE = "invalid_type"
    EMPTY = "empty"
    UNSUPPORTED_VERSION = "unsupported_version"
    TRUNCATED = "truncated"
    INVALID_READING_COUNT = "invalid_reading_count"
    LENGTH_MISMATCH = "length_mismatch"
    INVALID_COORDINATOR_UID = "invalid_coordinator_uid"
    INVALID_KEY_VERSION = "invalid_key_version"
    INVALID_CHANNEL = "invalid_channel"
    UNKNOWN_METRIC_CODE = "unknown_metric_code"
    DUPLICATE_CHANNEL = "duplicate_channel"


class LoraFrameError(ValueError):
    """Trama estructuralmente inválida. El mensaje es fijo y no incluye datos."""

    def __init__(self, reason: LoraFrameRejection) -> None:
        super().__init__(INVALID_FRAME_MESSAGE)
        self.reason = LoraFrameRejection(reason)

    def __repr__(self) -> str:
        return f"LoraFrameError(reason={self.reason.value!r})"


@dataclass(frozen=True, slots=True)
class LoraReading:
    """Lectura de un canal. ``raw_value`` es el entero escalado ×100 recibido."""

    channel: int
    metric: str
    raw_value: int

    @property
    def is_invalid_marker(self) -> bool:
        """Indica si el valor es el centinela reservado de la métrica.

        Solo lo identifica: aceptar, almacenar o descartar la lectura es una
        decisión pendiente de una etapa posterior.
        """
        return self.raw_value == _INVALID_MARKERS[self.metric]


@dataclass(frozen=True, slots=True)
class LoraFrameV1:
    """Trama v1 interpretada; sus campos no están autenticados todavía.

    Todos los campos salen de ``signed_bytes``; ``tag`` son los 32 bytes
    finales de la misma trama. ``repr`` omite el uid, los bytes firmados y el
    tag.
    """

    frame_version: int
    coordinator_uid: str = field(repr=False)
    key_version: int
    boot_counter: int
    seq: int
    readings: tuple[LoraReading, ...]
    signed_bytes: bytes = field(repr=False)
    tag: bytes = field(repr=False)

    def to_coordinator_frame(self):
        """Adapta la trama al ``ParsedCoordinatorFrame`` de IoT-03.2."""
        # Importación local: coordinator_credentials importa este módulo.
        from apps.iot.coordinator_credentials import ParsedCoordinatorFrame

        return ParsedCoordinatorFrame(
            coordinator_uid=self.coordinator_uid,
            key_version=self.key_version,
            boot_counter=self.boot_counter,
            seq=self.seq,
            signed_bytes=self.signed_bytes,
            tag=self.tag,
        )


def parse_lora_frame(raw) -> LoraFrameV1:
    """Interpreta una trama LoRa completa (``signed_bytes`` + tag).

    Acepta ``bytes`` o ``bytearray`` (este se copia a ``bytes`` inmutables).
    Lanza ``LoraFrameError`` ante cualquier desviación del contrato.
    """
    if isinstance(raw, (bytes, bytearray)):
        data = bytes(raw)
    else:
        raise LoraFrameError(LoraFrameRejection.INVALID_TYPE)
    if not data:
        raise LoraFrameError(LoraFrameRejection.EMPTY)
    if data[0] != FRAME_VERSION_V1:
        raise LoraFrameError(LoraFrameRejection.UNSUPPORTED_VERSION)
    return _parse_v1(data)


def _parse_v1(data: bytes) -> LoraFrameV1:
    if len(data) < HEADER_BYTES:
        raise LoraFrameError(LoraFrameRejection.TRUNCATED)
    count = data[HEADER_BYTES - 1]
    if not MIN_READINGS <= count <= MAX_READINGS_V1:
        raise LoraFrameError(LoraFrameRejection.INVALID_READING_COUNT)
    signed_length = HEADER_BYTES + READING_BYTES * count
    if len(data) != signed_length + TAG_BYTES:
        raise LoraFrameError(LoraFrameRejection.LENGTH_MISMATCH)

    signed_bytes = data[:signed_length]
    tag = data[signed_length:]

    version, uid_raw, key_version, boot_counter, seq, reading_count = _HEADER.unpack_from(
        signed_bytes, 0
    )
    coordinator_uid = _decode_uid(uid_raw)
    if key_version < MIN_KEY_VERSION:
        raise LoraFrameError(LoraFrameRejection.INVALID_KEY_VERSION)
    readings = _decode_readings(signed_bytes, reading_count)

    return LoraFrameV1(
        frame_version=version,
        coordinator_uid=coordinator_uid,
        key_version=key_version,
        boot_counter=boot_counter,
        seq=seq,
        readings=readings,
        signed_bytes=signed_bytes,
        tag=tag,
    )


def _decode_uid(uid_raw: bytes) -> str:
    """Valida el uid en forma canónica estricta, sin normalizar."""
    if len(uid_raw) != UID_BYTES or not uid_raw.isascii():
        raise LoraFrameError(LoraFrameRejection.INVALID_COORDINATOR_UID)
    uid = uid_raw.decode("ascii")
    if not is_valid_identifier(uid, IdentifierKind.COORDINATOR):
        raise LoraFrameError(LoraFrameRejection.INVALID_COORDINATOR_UID)
    return uid


def _decode_readings(signed_bytes: bytes, count: int) -> tuple[LoraReading, ...]:
    readings = []
    seen_channels = set()
    for index in range(count):
        offset = HEADER_BYTES + READING_BYTES * index
        channel, metric_code = _READING_HEAD.unpack_from(signed_bytes, offset)
        if channel < MIN_CHANNEL:
            raise LoraFrameError(LoraFrameRejection.INVALID_CHANNEL)
        wire = _WIRE_METRICS.get(metric_code)
        if wire is None:
            raise LoraFrameError(LoraFrameRejection.UNKNOWN_METRIC_CODE)
        if channel in seen_channels:
            raise LoraFrameError(LoraFrameRejection.DUPLICATE_CHANNEL)
        seen_channels.add(channel)
        (value,) = wire.value_format.unpack_from(signed_bytes, offset + _READING_HEAD.size)
        readings.append(LoraReading(channel=channel, metric=wire.metric, raw_value=value))
    return tuple(readings)


__all__ = [
    "FRAME_VERSION_V1",
    "HEADER_BYTES",
    "HUMIDITY_INVALID_MARKER",
    "LORA_FRAME_HMAC_CONTEXT",
    "LoraFrameError",
    "LoraFrameRejection",
    "LoraFrameV1",
    "LoraReading",
    "MAX_CHANNEL",
    "MAX_FRAME_BYTES_V1",
    "MAX_READINGS_V1",
    "METRIC_CODE_RELATIVE_HUMIDITY",
    "METRIC_CODE_TEMPERATURE",
    "METRIC_RELATIVE_HUMIDITY",
    "METRIC_TEMPERATURE",
    "MIN_CHANNEL",
    "MIN_FRAME_BYTES_V1",
    "MIN_READINGS",
    "READING_BYTES",
    "TAG_BYTES",
    "TEMPERATURE_INVALID_MARKER",
    "parse_lora_frame",
]
