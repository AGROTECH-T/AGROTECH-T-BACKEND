"""Ingesta transaccional de tramas LoRa recibidas por MQTT (IoT-04).

Una sola transacción cubre la autenticación con la credencial vigente, la
deduplicación, la ventana anti-replay, el mapeo de sensores y la persistencia de
``ReceivedFrame``/``Measurement``. Si algo falla, se deshace todo, incluida la
ventana de arranques.

Orden de bloqueo: Farm -> Coordinator -> Gateway -> CoordinatorGateway ->
CoordinatorCredential -> BootWindow -> Sensor. La credencial se vuelve a
verificar bajo bloqueo y el HMAC se comprueba en la misma transacción (M-03).

El broker autentica al gateway; el backend no recibe su secreto en el payload.
Por eso se revalida que exista una ``GatewayCredential`` activa para el gateway
resuelto por el topic, que es la parte verificable en este punto.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from django.db import IntegrityError, transaction

from apps.accounts.errors import AppError
from apps.farms.models import Farm
from apps.iot import coordinator_credentials as crypto
from apps.iot import lora_frame
from apps.iot.identifiers import IdentifierKind, is_valid_identifier
from apps.iot.ingestion_identity import (
    SERVICE_PRINCIPAL as INGESTION_SERVICE_PRINCIPAL,
    IngestionContractError,
    IngestionIdentity,
    IngestionRequest,
    ingestion_request_hash,
    verify_ingestion_request_hash,
)
from apps.iot.models import (
    ACTIVE_PAIR,
    ACTIVE_SLOT,
    VALUE_SCALE,
    Coordinator,
    CoordinatorCredential,
    CoordinatorGateway,
    FrameConflict,
    Gateway,
    GatewayCredential,
    Measurement,
    ReceivedFrame,
    Sensor,
    SensorSource,
)
from apps.iot.mqtt.payload import ValidatedPayload
from apps.iot.services.coordinator_credentials import (
    AuthenticatedCoordinatorFrame,
    SequenceOutOfWindow,
    accept_coordinator_sequence,
)
from config.coordinator_key_encryption_key import (
    UnsupportedMasterKeyVersion,
    load_master_key_ring,
)

logger = logging.getLogger(__name__)


class IngestionStatus(StrEnum):
    """Resultado tipado de una ingesta; todos son permanentes (PUBACK)."""

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


@dataclass(frozen=True, slots=True)
class IngestionResult:
    status: IngestionStatus
    frame_id: int | None = None
    measurement_count: int = 0
    authenticated_coordinator_id: int | None = None

    @property
    def permanent(self) -> bool:
        """Un resultado tipado no debe reintentarse; una excepción sí."""
        return True


# S1 (HARDEN-01): política explícita para la hora de medición.
# ``lora_rx_at`` del gateway solo se usa como ``measured_at`` si su reloj está
# sincronizado y cae en [server_received_at - MAX_AGE, server_received_at +
# MAX_FUTURE_SKEW]. Fuera de ese rango se usa ``server_received_at`` con
# ``measured_at_approximate=True``; el valor original siempre queda en
# ``ReceivedFrame.gateway_received_at``. Así una fecha futura no puede quedar
# como "última lectura" de forma permanente. El frame v1 no cambia.
MEASURED_AT_MAX_FUTURE_SKEW = timedelta(minutes=2)
MEASURED_AT_MAX_AGE = timedelta(days=30)


class MeasuredAtSource(StrEnum):
    GATEWAY = "gateway"
    SERVER_UNSYNCED = "server_unsynced"
    SERVER_FUTURE = "server_future"
    SERVER_TOO_OLD = "server_too_old"


def resolve_measured_at(
    *,
    lora_rx_at: datetime | None,
    clock_synced: bool,
    server_received_at: datetime,
) -> tuple[datetime, bool, MeasuredAtSource]:
    """Devuelve ``(measured_at, approximate, source)`` según la política S1."""
    if not clock_synced or lora_rx_at is None:
        return server_received_at, True, MeasuredAtSource.SERVER_UNSYNCED
    if lora_rx_at > server_received_at + MEASURED_AT_MAX_FUTURE_SKEW:
        return server_received_at, True, MeasuredAtSource.SERVER_FUTURE
    if lora_rx_at < server_received_at - MEASURED_AT_MAX_AGE:
        return server_received_at, True, MeasuredAtSource.SERVER_TOO_OLD
    return lora_rx_at, False, MeasuredAtSource.GATEWAY


class _Reject(Exception):
    def __init__(
        self,
        status: IngestionStatus,
        *,
        authenticated_coordinator_id: int | None = None,
    ) -> None:
        self.status = status
        self.authenticated_coordinator_id = authenticated_coordinator_id


def ingest(
    *,
    gateway_uid: str,
    payload: ValidatedPayload,
    server_received_at: datetime,
) -> IngestionResult:
    """Procesa un payload ya validado y devuelve un resultado tipado.

    Las excepciones no controladas (base de datos caída, error inesperado) se
    propagan para que el consumidor no confirme el mensaje.
    """
    if not isinstance(payload, ValidatedPayload):
        raise TypeError("se esperaba un payload validado")
    if not isinstance(server_received_at, datetime) or server_received_at.tzinfo is None:
        raise TypeError("server_received_at debe ser un datetime con zona")
    if not is_valid_identifier(gateway_uid, IdentifierKind.GATEWAY):
        return IngestionResult(IngestionStatus.REJECTED_INVALID_PAYLOAD)

    try:
        parsed = lora_frame.parse_lora_frame(payload.frame)
    except lora_frame.LoraFrameError:
        return IngestionResult(IngestionStatus.REJECTED_INVALID_PAYLOAD)

    try:
        with transaction.atomic():
            result = _ingest_in_transaction(
                gateway_uid=gateway_uid,
                payload=payload,
                parsed=parsed,
                server_received_at=server_received_at,
            )
    except _Reject as rejection:
        result = IngestionResult(
            rejection.status,
            authenticated_coordinator_id=rejection.authenticated_coordinator_id,
        )
    except SequenceOutOfWindow:
        result = IngestionResult(IngestionStatus.REJECTED_REPLAY)
    except AppError as exc:
        # Solo una denegación explícita de autorización es un rechazo de
        # dominio. Errores de configuración/estado interno deben propagarse.
        if exc.status_code != 403:
            raise
        result = IngestionResult(IngestionStatus.REJECTED_AUTHORIZATION)

    logger.info(
        "ingesta IoT %s gateway=%s frame=%s mediciones=%s",
        result.status.value,
        gateway_uid,
        result.frame_id,
        result.measurement_count,
    )
    return result


def _ingest_in_transaction(
    *,
    gateway_uid: str,
    payload: ValidatedPayload,
    parsed: lora_frame.LoraFrameV1,
    server_received_at: datetime,
) -> IngestionResult:
    gateway_row = Gateway.objects.filter(uid=gateway_uid).values_list("pk", "farm_id").first()
    if gateway_row is None:
        raise _Reject(IngestionStatus.REJECTED_GATEWAY)
    gateway_id, farm_id = gateway_row

    farm = Farm.objects.select_for_update().filter(pk=farm_id).first()
    if farm is None or not farm.is_active:
        raise _Reject(IngestionStatus.REJECTED_GATEWAY)

    coordinator = (
        Coordinator.objects.select_for_update()
        .filter(farm_id=farm_id, uid=parsed.coordinator_uid)
        .first()
    )
    if coordinator is None or not coordinator.is_active:
        raise _Reject(IngestionStatus.REJECTED_AUTHORIZATION)

    gateway = Gateway.objects.select_for_update().filter(pk=gateway_id, farm_id=farm_id).first()
    if gateway is None or gateway.uid != gateway_uid or not gateway.is_active:
        raise _Reject(IngestionStatus.REJECTED_GATEWAY)

    authorization = (
        CoordinatorGateway.objects.select_for_update()
        .filter(
            farm_id=farm_id,
            coordinator=coordinator,
            gateway=gateway,
            active_pair=ACTIVE_PAIR,
        )
        .first()
    )
    if authorization is None:
        raise _Reject(IngestionStatus.REJECTED_AUTHORIZATION)

    if not GatewayCredential.objects.filter(gateway=gateway, active_slot=ACTIVE_SLOT).exists():
        raise _Reject(IngestionStatus.REJECTED_GATEWAY)

    credential = (
        CoordinatorCredential.objects.select_for_update()
        .filter(coordinator=coordinator, active_slot=ACTIVE_SLOT)
        .first()
    )
    if credential is None:
        raise _Reject(IngestionStatus.REJECTED_CREDENTIAL)

    key = _decrypt_credential_key(credential, parsed)
    if not _verify_frame_hmac(key=key, parsed=parsed):
        raise _Reject(IngestionStatus.REJECTED_HMAC)
    if credential.key_version is None or parsed.key_version != credential.key_version:
        raise _Reject(IngestionStatus.REJECTED_GENERATION)

    try:
        identity, request_hash = _ingestion_request_fingerprint(
            coordinator=coordinator,
            credential=credential,
            gateway=gateway,
            parsed=parsed,
            payload=payload,
        )
    except IngestionContractError:
        raise _Reject(
            IngestionStatus.REJECTED_INVALID_PAYLOAD,
            authenticated_coordinator_id=coordinator.pk,
        ) from None

    # This stable UUID is a log correlation key, not an authenticator or a
    # replacement for the database UNIQUE constraint below.
    logger.debug(
        "identidad de ingesta validada operation_id=%s",
        identity.operation_id(),
    )

    existing = _find_identity(
        coordinator=coordinator,
        credential=credential,
        boot_counter=parsed.boot_counter,
        seq=parsed.seq,
    )
    if existing is not None:
        return _resolve_existing(
            existing=existing,
            gateway=gateway,
            farm_id=farm_id,
            payload=payload,
            server_received_at=server_received_at,
            request_hash=request_hash,
            authenticated_coordinator_id=coordinator.pk,
        )

    authenticated = AuthenticatedCoordinatorFrame(
        coordinator_id=coordinator.pk,
        coordinator_uid=coordinator.uid,
        farm_id=farm_id,
        credential_id=credential.pk,
        boot_counter=parsed.boot_counter,
        seq=parsed.seq,
        key_version=parsed.key_version,
    )
    try:
        accept_coordinator_sequence(frame=authenticated, now=server_received_at)
    except SequenceOutOfWindow:
        return IngestionResult(
            IngestionStatus.REJECTED_REPLAY,
            authenticated_coordinator_id=coordinator.pk,
        )

    selected = _map_sensors(
        coordinator=coordinator,
        parsed=parsed,
        authenticated_coordinator_id=coordinator.pk,
    )

    measured_at, measured_at_approximate, time_source = resolve_measured_at(
        lora_rx_at=payload.lora_rx_at,
        clock_synced=payload.clock_synced,
        server_received_at=server_received_at,
    )
    if time_source in (MeasuredAtSource.SERVER_FUTURE, MeasuredAtSource.SERVER_TOO_OLD):
        logger.warning(
            "lora_rx_at fuera de tolerancia (%s) gateway=%s; se usa la hora del servidor "
            "y se conserva la original en gateway_received_at",
            time_source.value,
            gateway_uid,
        )

    try:
        with transaction.atomic():
            frame_row = _create_frame_row(
                farm_id=farm_id,
                gateway=gateway,
                coordinator=coordinator,
                credential=credential,
                authorization=authorization,
                parsed=parsed,
                payload=payload,
                server_received_at=server_received_at,
                measured_at=measured_at,
                measured_at_approximate=measured_at_approximate,
            )
    except IntegrityError:
        existing = _find_identity(
            coordinator=coordinator,
            credential=credential,
            boot_counter=parsed.boot_counter,
            seq=parsed.seq,
        )
        if existing is None:
            raise
        return _resolve_existing(
            existing=existing,
            gateway=gateway,
            farm_id=farm_id,
            payload=payload,
            server_received_at=server_received_at,
            request_hash=request_hash,
            authenticated_coordinator_id=coordinator.pk,
        )

    measurements = [
        Measurement(
            frame=frame_row,
            farm_id=farm_id,
            coordinator=coordinator,
            sensor=sensor,
            channel=reading.channel,
            metric=reading.metric,
            raw_value=reading.raw_value,
            value=(None if reading.is_invalid_marker else Decimal(reading.raw_value) / VALUE_SCALE),
            is_invalid_marker=reading.is_invalid_marker,
            measured_at=measured_at,
        )
        for reading, sensor in selected
    ]
    if measurements:
        _create_measurements(measurements)

    return IngestionResult(
        IngestionStatus.ACCEPTED,
        frame_id=frame_row.pk,
        measurement_count=len(measurements),
        authenticated_coordinator_id=coordinator.pk,
    )


def _decrypt_credential_key(
    credential: CoordinatorCredential, parsed: lora_frame.LoraFrameV1
) -> bytes:
    key_ring = load_master_key_ring()
    if not crypto.master_key_version_supported(key_ring, credential.master_key_version):
        raise _Reject(IngestionStatus.REJECTED_CREDENTIAL)
    try:
        return crypto.decrypt_coordinator_key(
            credential.ciphertext,
            credential.nonce,
            coordinator_uid=parsed.coordinator_uid,
            key_ring=key_ring,
            master_key_version=credential.master_key_version,
        )
    except (
        crypto.CoordinatorCredentialUnreadable,
        crypto.InvalidEncryptedCredential,
        crypto.InvalidCoordinatorKey,
        UnsupportedMasterKeyVersion,
    ):
        raise _Reject(IngestionStatus.REJECTED_CREDENTIAL) from None


def _verify_frame_hmac(*, key: bytes, parsed: lora_frame.LoraFrameV1) -> bool:
    return crypto.verify_lora_hmac(
        key,
        parsed.signed_bytes,
        parsed.tag,
        context=lora_frame.LORA_FRAME_HMAC_CONTEXT,
    )


def _find_identity(
    *,
    coordinator: Coordinator,
    credential: CoordinatorCredential,
    boot_counter: int,
    seq: int,
) -> ReceivedFrame | None:
    return ReceivedFrame.objects.filter(
        coordinator=coordinator,
        credential=credential,
        boot_counter=boot_counter,
        seq=seq,
    ).first()


def _ingestion_request_fingerprint(
    *,
    coordinator: Coordinator,
    credential: CoordinatorCredential,
    gateway: Gateway,
    parsed: lora_frame.LoraFrameV1,
    payload: ValidatedPayload,
) -> tuple[IngestionIdentity, str]:
    """Build and verify the canonical request after gateway/coordinator HMAC auth.

    The principal is fixed by this trusted Django service context; it is never
    read from MQTT or caller-supplied data. UUID/hash values correlate the
    operation and compare retries, but do not authenticate a frame or authorize
    a database write.
    """
    identity = IngestionIdentity(
        coordinator_id=coordinator.pk,
        credential_id=credential.pk,
        boot_counter=parsed.boot_counter,
        seq=parsed.seq,
    )
    request = IngestionRequest(
        identity=identity,
        gateway_uid=gateway.uid,
        raw_frame=bytes(payload.frame),
        clock_synced=payload.clock_synced,
        buffered=payload.buffered,
        lora_rx_at=payload.lora_rx_at,
        rssi=payload.rssi,
        snr=payload.snr,
    )
    digest = ingestion_request_hash(
        request,
        authenticated_principal=INGESTION_SERVICE_PRINCIPAL,
    )
    # The in-process API has no caller-supplied digest. Verify the locally
    # derived canonical fingerprint before it participates in deduplication.
    verify_ingestion_request_hash(
        request,
        authenticated_principal=INGESTION_SERVICE_PRINCIPAL,
        supplied_hash=digest,
    )
    return identity, digest


def _resolve_existing(
    *,
    existing: ReceivedFrame,
    gateway: Gateway,
    farm_id: int,
    payload: ValidatedPayload,
    server_received_at: datetime,
    request_hash: str,
    authenticated_coordinator_id: int,
) -> IngestionResult:
    existing_identity = IngestionIdentity(
        coordinator_id=existing.coordinator_id,
        credential_id=existing.credential_id,
        boot_counter=existing.boot_counter,
        seq=existing.seq,
    )
    existing_request = IngestionRequest(
        identity=existing_identity,
        gateway_uid=existing.gateway.uid,
        raw_frame=bytes(existing.raw_frame),
        clock_synced=existing.gateway_clock_synced,
        buffered=existing.buffered,
        lora_rx_at=existing.gateway_received_at,
        rssi=existing.rssi,
        snr=existing.snr,
    )
    existing_hash = ingestion_request_hash(
        existing_request,
        authenticated_principal=INGESTION_SERVICE_PRINCIPAL,
    )
    if secrets.compare_digest(existing_hash, request_hash):
        return IngestionResult(
            IngestionStatus.DUPLICATE,
            frame_id=existing.pk,
            authenticated_coordinator_id=authenticated_coordinator_id,
        )
    if bytes(existing.raw_frame) != payload.frame:
        _record_conflict(
            existing=existing,
            gateway=gateway,
            farm_id=farm_id,
            conflicting_frame=payload.frame,
            server_received_at=server_received_at,
        )
    else:
        logger.warning(
            "conflicto de metadatos de ingesta operation_id=%s frame=%s",
            existing_identity.operation_id(),
            existing.pk,
        )
    return IngestionResult(
        IngestionStatus.CONFLICT,
        frame_id=existing.pk,
        authenticated_coordinator_id=authenticated_coordinator_id,
    )


def _record_conflict(
    *,
    existing: ReceivedFrame,
    gateway: Gateway,
    farm_id: int,
    conflicting_frame: bytes,
    server_received_at: datetime,
) -> None:
    digest = hashlib.sha256(conflicting_frame).hexdigest()
    try:
        with transaction.atomic():
            FrameConflict.objects.create(
                frame=existing,
                gateway=gateway,
                farm_id=farm_id,
                conflicting_raw_frame=conflicting_frame,
                conflicting_sha256=digest,
                server_received_at=server_received_at,
            )
    except IntegrityError:
        if not FrameConflict.objects.filter(frame=existing, conflicting_sha256=digest).exists():
            raise


def _map_sensors(
    *,
    coordinator: Coordinator,
    parsed: lora_frame.LoraFrameV1,
    authenticated_coordinator_id: int,
) -> list[tuple[lora_frame.LoraReading, Sensor]]:
    channels = {reading.channel for reading in parsed.readings}
    sensors = list(
        Sensor.objects.select_for_update()
        .filter(coordinator=coordinator, channel__in=channels)
        .order_by("pk")
    )
    by_channel: dict[int, list[Sensor]] = {}
    for sensor in sensors:
        by_channel.setdefault(sensor.channel, []).append(sensor)

    active_source_ids = sorted(
        {
            sensor.source_id
            for sensor in sensors
            if sensor.is_active and sensor.source_id is not None
        }
    )
    sources = {
        source.pk: source
        for source in SensorSource.objects.select_for_update()
        .filter(pk__in=active_source_ids)
        .order_by("pk")
    }

    selected: list[tuple[lora_frame.LoraReading, Sensor]] = []
    for reading in parsed.readings:
        rows = by_channel.get(reading.channel)
        if not rows:
            raise _Reject(
                IngestionStatus.REJECTED_SENSOR_MAPPING,
                authenticated_coordinator_id=authenticated_coordinator_id,
            )
        active = [sensor for sensor in rows if sensor.is_active]
        if active:
            match = next((sensor for sensor in active if sensor.metric == reading.metric), None)
            if match is None:
                raise _Reject(
                    IngestionStatus.REJECTED_SENSOR_MAPPING,
                    authenticated_coordinator_id=authenticated_coordinator_id,
                )
            if match.source_id is not None:
                source = sources.get(match.source_id)
                if source is None or not source.is_active:
                    raise _Reject(
                        IngestionStatus.REJECTED_SENSOR_MAPPING,
                        authenticated_coordinator_id=authenticated_coordinator_id,
                    )
            selected.append((reading, match))
        elif not any(sensor.metric == reading.metric for sensor in rows):
            raise _Reject(
                IngestionStatus.REJECTED_SENSOR_MAPPING,
                authenticated_coordinator_id=authenticated_coordinator_id,
            )
    return selected


def _create_frame_row(
    *,
    farm_id: int,
    gateway: Gateway,
    coordinator: Coordinator,
    credential: CoordinatorCredential,
    authorization: CoordinatorGateway,
    parsed: lora_frame.LoraFrameV1,
    payload: ValidatedPayload,
    server_received_at: datetime,
    measured_at: datetime,
    measured_at_approximate: bool,
) -> ReceivedFrame:
    return ReceivedFrame.objects.create(
        farm_id=farm_id,
        gateway=gateway,
        coordinator=coordinator,
        credential=credential,
        authorization=authorization,
        frame_version=parsed.frame_version,
        key_version=parsed.key_version,
        boot_counter=parsed.boot_counter,
        seq=parsed.seq,
        reading_count=len(parsed.readings),
        raw_frame=payload.frame,
        gateway_received_at=payload.lora_rx_at,
        gateway_clock_synced=payload.clock_synced,
        buffered=payload.buffered,
        rssi=payload.rssi,
        snr=payload.snr,
        server_received_at=server_received_at,
        measured_at=measured_at,
        measured_at_approximate=measured_at_approximate,
    )


def _create_measurements(rows: list[Measurement]) -> None:
    Measurement.objects.bulk_create(rows)


__all__ = ["IngestionResult", "IngestionStatus", "ingest"]
