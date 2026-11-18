"""Stable identity and request hashing contract for IoT ingestion.

This module is deliberately pure: it performs no I/O, database access, HMAC
verification, or transport authentication. It validates the frame structure
using the existing Frame v1 parser. Callers remain responsible for authenticating
the frame and supplying a trusted principal. These helpers do not authenticate
a transport or implement a Writer service.

The UUIDv5 name format and the ``iot-request-v1`` document are persistent
contracts. Changing either requires a new, explicitly compatible contract;
old operation IDs and request hashes must remain resolvable indefinitely.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from uuid import NAMESPACE_URL, UUID, uuid5

from apps.iot import lora_frame
from apps.iot.identifiers import IdentifierKind, is_valid_identifier

OPERATION_TYPE = "persist_ingestion"
SERVICE_PRINCIPAL = "django-iot-ingestion"
ACTOR_KIND = "system"
ACTOR_ID = "iot-ingestion"
REQUEST_HASH_VERSION = "iot-request-v1"
UUID_NAMESPACE_NAME = "urn:agrotech:iot:persist-ingestion:v1"
UUID_NAMESPACE = uuid5(NAMESPACE_URL, UUID_NAMESPACE_NAME)

BIGINT_MAX = (1 << 63) - 1
UINT32_MAX = (1 << 32) - 1
RSSI_MIN_DBM = -164
RSSI_MAX_DBM = 0
SNR_MIN_DB = Decimal("-20.00")
SNR_MAX_DB = Decimal("20.00")
SNR_QUANTUM = Decimal("0.01")


class IngestionContractError(ValueError):
    """An ingestion identity or canonical request violates this contract."""


@dataclass(frozen=True, slots=True)
class IngestionIdentity:
    """Natural frame identity used to derive the stable operation UUID."""

    coordinator_id: int
    credential_id: int
    boot_counter: int
    seq: int

    def __post_init__(self) -> None:
        _require_positive_bigint(self.coordinator_id, "coordinator_id")
        _require_positive_bigint(self.credential_id, "credential_id")
        _require_uint32(self.boot_counter, "boot_counter")
        _require_uint32(self.seq, "seq")

    def canonical_name(self) -> str:
        """Return the exact JSON name string used by the UUIDv5 contract."""
        document = {
            "boot_counter": str(self.boot_counter),
            "coordinator_id": str(self.coordinator_id),
            "credential_id": str(self.credential_id),
            "seq": str(self.seq),
        }
        return canonical_json_bytes(document).decode("utf-8")

    def operation_id(self) -> UUID:
        """Return the permanent UUIDv5 for this natural identity."""
        return uuid5(UUID_NAMESPACE, self.canonical_name())


@dataclass(frozen=True, slots=True)
class IngestionRequest:
    """Semantic request values included in ``iot-request-v1``.

    ``server_received_at``, correlation IDs, MQTT packet IDs, certificate
    fingerprints, and instance identifiers are intentionally absent. The
    helper does not persist this request or its result. The current ingestion
    path reconstructs accepted requests from ``ReceivedFrame`` when comparing
    redeliveries; rejected results are not durable.
    """

    identity: IngestionIdentity
    gateway_uid: str
    raw_frame: bytes
    clock_synced: bool
    buffered: bool
    lora_rx_at: datetime | None
    rssi: int | None
    snr: Decimal | None


def canonical_ingestion_document(
    request: IngestionRequest,
    *,
    authenticated_principal: str,
) -> dict[str, object]:
    """Build the canonical hash document using the authenticated principal.

    ``authenticated_principal`` is an explicit trusted value supplied by the
    caller, never read from a request body. In this contract only the approved
    ingestion principal and fixed automatic actor are accepted. This check does
    not itself authenticate a caller or transport.
    """
    if (
        type(authenticated_principal) is not str
        or authenticated_principal != SERVICE_PRINCIPAL
    ):
        raise IngestionContractError("principal de servicio no autorizado")
    _validate_request(request)

    actor = {"id": ACTOR_ID, "kind": ACTOR_KIND, "user_id": None}
    semantic_request: dict[str, object] = {
        "boot_counter": str(request.identity.boot_counter),
        "buffered": request.buffered,
        "clock_synced": request.clock_synced,
        "coordinator_id": str(request.identity.coordinator_id),
        "credential_id": str(request.identity.credential_id),
        "frame": base64.b64encode(request.raw_frame).decode("ascii"),
        "gateway_uid": request.gateway_uid,
        "lora_rx_at": _canonical_timestamp(request.lora_rx_at),
        "rssi": request.rssi,
        "seq": str(request.identity.seq),
        "snr": _canonical_snr(request.snr),
    }
    return {
        "actor": actor,
        "operation_type": OPERATION_TYPE,
        "principal": authenticated_principal,
        "request": semantic_request,
        "version": REQUEST_HASH_VERSION,
    }


def canonical_ingestion_request_bytes(
    request: IngestionRequest,
    *,
    authenticated_principal: str,
) -> bytes:
    """Serialize the request document to its exact UTF-8 canonical bytes."""
    return canonical_json_bytes(
        canonical_ingestion_document(
            request,
            authenticated_principal=authenticated_principal,
        )
    )


def ingestion_request_hash(
    request: IngestionRequest,
    *,
    authenticated_principal: str,
) -> str:
    """Return the lowercase SHA-256 hex digest of the canonical request."""
    canonical = canonical_ingestion_request_bytes(
        request,
        authenticated_principal=authenticated_principal,
    )
    return hashlib.sha256(canonical).hexdigest()


def verify_ingestion_request_hash(
    request: IngestionRequest,
    *,
    authenticated_principal: str,
    supplied_hash: str,
) -> None:
    """Recompute and compare a supplied digest without trusting the caller."""
    if not isinstance(supplied_hash, str) or len(supplied_hash) != 64:
        raise IngestionContractError("request_hash no válido")
    try:
        supplied = bytes.fromhex(supplied_hash)
    except ValueError:
        raise IngestionContractError("request_hash no válido") from None
    if supplied_hash != supplied_hash.lower() or len(supplied) != 32:
        raise IngestionContractError("request_hash no válido")

    expected = bytes.fromhex(
        ingestion_request_hash(
            request,
            authenticated_principal=authenticated_principal,
        )
    )
    if not secrets.compare_digest(expected, supplied):
        raise IngestionContractError("request_hash no coincide")


def canonical_json_bytes(value: object) -> bytes:
    """Serialize supported JSON values deterministically as UTF-8.

    Object keys are sorted at every level, strings are NFC-normalized,
    non-finite numbers and floats are rejected, and insignificant whitespace
    is omitted. Contract-specific decimal values must already be strings.
    """
    normalized = _normalize_json_value(value)
    try:
        text = json.dumps(
            normalized,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return text.encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeEncodeError):
        raise IngestionContractError("documento canónico no válido") from None


def _normalize_json_value(value: object) -> object:
    if value is None or type(value) in (bool, int):
        return value
    if isinstance(value, float) or isinstance(value, Decimal):
        raise IngestionContractError("no se permiten floats ni Decimal JSON")
    if isinstance(value, str):
        normalized = unicodedata.normalize("NFC", value)
        try:
            normalized.encode("utf-8", errors="strict")
        except UnicodeEncodeError:
            raise IngestionContractError("texto Unicode no válido") from None
        return normalized
    if isinstance(value, (list, tuple)):
        return [_normalize_json_value(item) for item in value]
    if isinstance(value, dict):
        normalized_dict: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise IngestionContractError("las claves JSON deben ser texto")
            normalized_key = unicodedata.normalize("NFC", key)
            if normalized_key in normalized_dict:
                raise IngestionContractError("claves duplicadas tras normalización")
            normalized_dict[normalized_key] = _normalize_json_value(item)
        return normalized_dict
    raise IngestionContractError("tipo no admitido en JSON canónico")


def _validate_request(request: IngestionRequest) -> None:
    if not isinstance(request, IngestionRequest):
        raise IngestionContractError("request de ingesta no válido")
    if not isinstance(request.identity, IngestionIdentity):
        raise IngestionContractError("identidad natural no válida")
    if not is_valid_identifier(request.gateway_uid, IdentifierKind.GATEWAY):
        raise IngestionContractError("gateway_uid no válido")
    if type(request.raw_frame) is not bytes or not request.raw_frame:
        raise IngestionContractError("raw_frame no válido")
    try:
        parsed = lora_frame.parse_lora_frame(request.raw_frame)
    except lora_frame.LoraFrameError:
        raise IngestionContractError("raw_frame no válido") from None
    if (
        parsed.boot_counter != request.identity.boot_counter
        or parsed.seq != request.identity.seq
    ):
        raise IngestionContractError("identidad no coincide con raw_frame")
    if type(request.clock_synced) is not bool or type(request.buffered) is not bool:
        raise IngestionContractError("flags de recepción no válidos")
    if request.lora_rx_at is not None:
        _canonical_timestamp(request.lora_rx_at)
    if request.rssi is not None:
        if type(request.rssi) is not int or not RSSI_MIN_DBM <= request.rssi <= RSSI_MAX_DBM:
            raise IngestionContractError("rssi no válido")
    if request.snr is not None:
        _canonical_snr(request.snr)


def _require_positive_bigint(value: int, field: str) -> None:
    if type(value) is not int or not 1 <= value <= BIGINT_MAX:
        raise IngestionContractError(f"{field} no válido")


def _require_uint32(value: int, field: str) -> None:
    if type(value) is not int or not 0 <= value <= UINT32_MAX:
        raise IngestionContractError(f"{field} no válido")


def _canonical_timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise IngestionContractError("lora_rx_at debe incluir zona horaria")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _canonical_snr(value: Decimal | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, Decimal) or not value.is_finite():
        raise IngestionContractError("snr debe ser Decimal finito")
    try:
        quantized = value.quantize(SNR_QUANTUM)
    except InvalidOperation:
        raise IngestionContractError("snr no válido") from None
    if quantized != value or not SNR_MIN_DB <= quantized <= SNR_MAX_DB:
        raise IngestionContractError("snr fuera de escala o rango")
    return format(quantized, ".2f")


__all__ = [
    "ACTOR_ID",
    "ACTOR_KIND",
    "BIGINT_MAX",
    "IngestionContractError",
    "IngestionIdentity",
    "IngestionRequest",
    "OPERATION_TYPE",
    "REQUEST_HASH_VERSION",
    "SERVICE_PRINCIPAL",
    "UINT32_MAX",
    "UUID_NAMESPACE",
    "UUID_NAMESPACE_NAME",
    "canonical_ingestion_document",
    "canonical_ingestion_request_bytes",
    "canonical_json_bytes",
    "ingestion_request_hash",
    "verify_ingestion_request_hash",
]
