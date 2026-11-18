"""Identidad del broker MQTT en modo record (BUILD-FLEPSI-02 / 02H).

- Recepción: ``prepare_new_delivery_evidence`` (puro, antes de la transacción)
  y ``observe_redelivery_safely`` (dentro de la transacción, con la fila ya
  bloqueada). Un error **ajeno a la base de datos** en la observación de
  identidad nunca impide el ACK: se aísla en un *savepoint*, no altera la
  evidencia guardada y deja ``BROKER_IDENTITY_OBSERVATION_ERROR``. Un
  ``DatabaseError`` se propaga siempre (sin ACK).
- ``identity_verdict`` / ``record_identity_verdict``: veredicto del worker
  sobre la fila bloqueada, comparando huellas guardadas (sin claves ni token).
  En record solo deja ``BROKER_IDENTITY_WOULD_REJECT``; nunca rechaza.
- Registro y rotación de identidades: solo huellas por versión, nunca el token.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass
from enum import StrEnum

from django.db import DEFAULT_DB_ALIAS, DatabaseError, IntegrityError, transaction
from django.db.models import Count, Q
from django.utils import timezone

from apps.iot.broker_identity_config import BrokerIdentityKeyRing, UnavailableKeyVersion
from apps.iot.db_guard import require_mariadb
from apps.iot.models import (
    ACTIVE_SLOT,
    BrokerIdentity,
    BrokerIdentityFingerprint,
    BrokerIdentityRevocationCause,
    Gateway,
    IotMqttDelivery,
    IotMqttDeliveryEvent,
)
from apps.iot.models import (
    IotMqttDeliveryState as State,
)
from apps.iot.mqtt.broker_identity import (
    PROVIDER_FLESPI,
    BrokerIdentityObservation,
    BrokerIdentityObservationFailure,
    BrokerIdentityPresence,
    classify_user_properties,
    short_fingerprint,
    stored_evidence_digest,
    token_fingerprint,
)
from apps.iot.mqtt.payload import TopicError, parse_topic

logger = logging.getLogger(__name__)

MAX_IDENTITY_ANOMALIES = 5
# H-02: evidencias distintas posteriores al límite registradas de forma exacta
# antes de saturar; protege la tabla frente a variaciones ilimitadas.
MAX_OVERFLOW_EVIDENCES = 64
DEDUPE_CONSTRAINT = "iot_mqtt_event_dedupe_uniq"
MYSQL_DUPLICATE_ENTRY = 1062
FINAL_STATES = frozenset(
    {State.ACCEPTED, State.DUPLICATE, State.REJECTED, State.CONFLICT, State.RESOLVED}
)
NON_FINAL_STATES = (State.RECEIVED, State.PROCESSING, State.RETRY_PENDING, State.DEAD_LETTER)

EVENT_MISMATCH = "BROKER_IDENTITY_MISMATCH"
EVENT_POSTFINAL = "BROKER_IDENTITY_POSTFINAL"
EVENT_LATE = "BROKER_IDENTITY_LATE"
EVENT_UNVERIFIABLE = "BROKER_IDENTITY_UNVERIFIABLE"
EVENT_LIMIT = "BROKER_IDENTITY_LIMIT"
EVENT_OVERFLOW = "BROKER_IDENTITY_OVERFLOW"
EVENT_OVERFLOW_CAP = "BROKER_IDENTITY_OVERFLOW_CAP"
EVENT_OBSERVATION_ERROR = "BROKER_IDENTITY_OBSERVATION_ERROR"
EVENT_WOULD_REJECT = "BROKER_IDENTITY_WOULD_REJECT"


class BrokerIdentityError(ValueError):
    """Operación administrativa de identidad no permitida (sin secretos)."""


# --------------------------------------------------------------------------
# Recepción: evidencia nueva y anomalías de reentrega
# --------------------------------------------------------------------------


def prepare_new_delivery_evidence(observation):
    """``(campos de evidencia, fallo | None)`` para una entrega nueva.

    Puro (sin base de datos): se llama antes de la transacción. Si el cálculo
    falla, la evidencia queda NULL (nunca se inventa una identidad) y el fallo
    se registra como evento en la misma transacción de recepción.
    """
    if observation is None:
        return {}, None
    if isinstance(observation, BrokerIdentityObservationFailure):
        return {}, observation
    try:
        presence, version, fingerprint = observation.evidence_for_new_delivery()
    except Exception as exc:  # noqa: BLE001 - frontera de observación (código puro)
        logger.error("evidencia de identidad no calculada (%s)", type(exc).__name__)
        return {}, BrokerIdentityObservationFailure("evidence", type(exc).__name__)
    return {
        "broker_identity_presence": presence,
        "broker_token_key_version": version,
        "broker_token_fingerprint": fingerprint,
    }, None


PHASE_RECEIPT = "receipt"
PHASE_REDELIVERY = "redelivery"


RESULT_CODE_MAX_LENGTH = 64  # IotMqttDeliveryEvent.result_code (max_length=64)
_RESULT_CODE_DIGEST_CHARS = 16


def diagnostic_result_code(phase: str, stage: str, error_type: str) -> str:
    """``<fase>:<etapa>:<tipo>`` acotado a 64 caracteres sin fusionar diagnósticos.

    BUILD-FLEPSI-02K (K-H4): si el valor completo cabe se usa tal cual (el
    formato existente no cambia). Si no, se conserva un prefijo legible y se
    añade ``~`` + 16 hex de SHA-256 del valor completo, de modo que dos tipos de
    error con el mismo prefijo truncado siguen siendo distintos y el resultado
    es determinista (la deduplicación J-06 se mantiene). El prefijo de fase se
    conserva siempre (``observation_failed_at_receipt`` depende de él).
    """
    full = f"{phase}:{stage}:{error_type}"
    if len(full) <= RESULT_CODE_MAX_LENGTH:
        return full
    digest = hashlib.sha256(full.encode("utf-8")).hexdigest()[:_RESULT_CODE_DIGEST_CHARS]
    keep = RESULT_CODE_MAX_LENGTH - 1 - _RESULT_CODE_DIGEST_CHARS
    return f"{full[:keep]}~{digest}"


def record_observation_failure(row: IotMqttDelivery, failure, *, phase: str) -> None:
    """Evento de diagnóstico sin valores, deduplicado; los errores de BD se propagan.

    BUILD-FLEPSI-02J (J-06): clave estable ``(entrega, event_type, result_code)``
    con ``result_code = "<fase>:<etapa>:<tipo de error>"``. Un diagnóstico
    idéntico no se repite; uno distinto (otra etapa o tipo) sí se registra.
    Sin índice nuevo: la recepción nueva lo inserta en la transacción que crea
    la entrega y la reentrega lo hace con la fila bloqueada (``select_for_update``),
    así que la comprobación previa es serializable. ``dedupe_key`` queda NULL.

    La fase ``receipt`` (J-01) marca que la observación falló en la recepción
    original: la evidencia NULL de esa entrega es "no observada", no histórica.
    """
    if phase not in (PHASE_RECEIPT, PHASE_REDELIVERY):
        raise ValueError("fase de observación no válida")
    result_code = diagnostic_result_code(phase, failure.stage, failure.error_type)
    if IotMqttDeliveryEvent.objects.filter(
        delivery=row, event_type=EVENT_OBSERVATION_ERROR, result_code=result_code
    ).exists():
        return
    IotMqttDeliveryEvent.objects.create(
        delivery=row,
        event_type=EVENT_OBSERVATION_ERROR,
        from_state=row.state,
        to_state=row.state,
        result_code=result_code,
        correlation_id=row.id,
        safe_detail=f"observación de identidad fallida ({failure.error_type})"[:1000],
    )


def observation_failed_at_receipt(row: IotMqttDelivery) -> bool:
    """J-01: evidencia NULL por fallo de observación en la recepción original.

    Determinista: ese diagnóstico solo se crea en la transacción que crea la
    entrega y los eventos son append-only (ORM + triggers).
    """
    return (
        row.broker_identity_presence is None
        and IotMqttDeliveryEvent.objects.filter(
            delivery=row,
            event_type=EVENT_OBSERVATION_ERROR,
            result_code__startswith=f"{PHASE_RECEIPT}:",
        ).exists()
    )


def observe_redelivery_safely(row: IotMqttDelivery, observation) -> None:
    """Aísla la observación de una reentrega: solo la BD puede impedir el ACK.

    Un error ajeno a la base de datos revierte solo el *savepoint* de identidad
    (evidencia y contadores intactos) y deja un evento de diagnóstico.
    ``DatabaseError`` (incluido ``IntegrityError``) se propaga sin cambios.
    """
    if isinstance(observation, BrokerIdentityObservationFailure):
        record_observation_failure(row, observation, phase=PHASE_REDELIVERY)
        return
    try:
        with transaction.atomic():
            record_redelivery_identity(row, observation)
    except DatabaseError:
        raise
    except Exception as exc:  # noqa: BLE001 - frontera de observación auxiliar
        logger.error(
            "observación de identidad fallida en reentrega %s (%s)", row.pk, type(exc).__name__
        )
        row.refresh_from_db(fields=["broker_identity_conflict", "broker_identity_overflow"])
        record_observation_failure(
            row,
            BrokerIdentityObservationFailure("record", type(exc).__name__),
            phase=PHASE_REDELIVERY,
        )


def record_redelivery_identity(
    row: IotMqttDelivery, observation: BrokerIdentityObservation
) -> None:
    """Registra, como mucho, un evento de identidad por evidencia distinta.

    Precondición: ``row`` fue leída con ``select_for_update`` en la transacción
    actual. Las filas con evidencia NULL nunca se modifican.
    """
    incoming_class = observation.presence.value
    if row.broker_identity_presence is None:
        if observation_failed_at_receipt(row):
            # J-01: no es una entrega histórica; el fallo original ya consta en
            # su diagnóstico y no hay evidencia guardada con la que comparar.
            return
        _insert_once(
            row,
            "bi:late",
            EVENT_LATE,
            incoming_class,
            "evidencia no capturada en la recepción original (histórica o modo off)",
        )
        return
    version = row.broker_token_key_version
    try:
        incoming = observation.digest(version)
        stored = stored_evidence_digest(
            observation.keyring.key_for(version),
            BrokerIdentityPresence(row.broker_identity_presence),
            None if row.broker_token_fingerprint is None else bytes(row.broker_token_fingerprint),
        )
    except UnavailableKeyVersion:
        _insert_once(
            row,
            f"bi:unverifiable:v{version}",
            EVENT_UNVERIFIABLE,
            incoming_class,
            f"clave de identidad v{version} no disponible",
        )
        return
    if hmac.compare_digest(incoming, stored):
        return
    kind = "postfinal" if row.state in FINAL_STATES else "mismatch"
    evidence_id = f"v{version}:{incoming.hex()}"
    dedupe_key = f"bi:{kind}:{evidence_id}"
    if _event_exists(row, dedupe_key):
        return
    # J-03: la evidencia se identifica por su huella versionada, no por la
    # categoría. Si ya se contabilizó en otra categoría no es nueva.
    seen = _categories_for_evidence(row, evidence_id)
    if seen:
        # K-H3: tras el estado final, una evidencia ya registrada (como mismatch
        # o como overflow) deja un único evento contextual que no cuenta.
        if kind == "postfinal":
            _record_context(row, dedupe_key, incoming_class, incoming, seen)
        return
    if len(_counted_evidences(row)) >= MAX_IDENTITY_ANOMALIES:
        _record_overflow(row, version, incoming, incoming_class)
        return
    detail = (
        f"guardada={row.broker_identity_presence}/v{version}/{short_fingerprint(stored)} "
        f"recibida={incoming_class}/{short_fingerprint(incoming)}"
    )
    try:
        with transaction.atomic():
            if kind == "mismatch" and not row.broker_identity_conflict:
                row.broker_identity_conflict = True
                row.save(update_fields=["broker_identity_conflict", "updated_at"])
            _create_event(
                row,
                dedupe_key,
                EVENT_POSTFINAL if kind == "postfinal" else EVENT_MISMATCH,
                incoming_class,
                detail,
            )
    except IntegrityError as exc:
        row.refresh_from_db(fields=["broker_identity_conflict"])
        if not _is_dedupe_collision(exc, row, dedupe_key):
            raise
        return
    if kind == "postfinal":
        logger.error(
            "identidad de broker distinta en reentrega de entrega final %s (%s); "
            "estado y mediciones sin cambios",
            row.pk,
            row.state,
        )


def _record_overflow(row, version, incoming, incoming_class) -> None:
    """H-02: el contador sube una sola vez por evidencia distinta tras el límite."""
    _insert_once(row, "bi:limit", EVENT_LIMIT, incoming_class, "límite de anomalías alcanzado")
    overflow_key = f"bi:overflow:v{version}:{incoming.hex()}"
    if _event_exists(row, overflow_key):
        return  # misma evidencia repetida: sin cambios
    if _count_events(row, "bi:overflow:") >= MAX_OVERFLOW_EVIDENCES:
        _insert_once(
            row,
            "bi:overflowcap",
            EVENT_OVERFLOW_CAP,
            incoming_class,
            f"más de {MAX_OVERFLOW_EVIDENCES} evidencias distintas tras el límite",
        )
        return
    try:
        with transaction.atomic():
            _create_event(
                row,
                overflow_key,
                EVENT_OVERFLOW,
                incoming_class,
                f"evidencia distinta tras el límite: {short_fingerprint(incoming)}",
            )
            row.broker_identity_overflow += 1
            row.save(update_fields=["broker_identity_overflow", "updated_at"])
    except IntegrityError as exc:
        row.refresh_from_db(fields=["broker_identity_overflow"])
        if not _is_dedupe_collision(exc, row, overflow_key):
            raise


COUNTED_KINDS = ("mismatch", "postfinal")
EVIDENCE_KINDS = ("mismatch", "postfinal", "overflow")


def _categories_for_evidence(row, evidence_id: str) -> set[str]:
    """Categorías en las que ya consta esta huella versionada (J-03)."""
    keys = [f"bi:{kind}:{evidence_id}" for kind in EVIDENCE_KINDS]
    found = IotMqttDeliveryEvent.objects.filter(delivery=row, dedupe_key__in=keys).values_list(
        "dedupe_key", flat=True
    )
    return {key.split(":", 2)[1] for key in found}


def _counted_evidences(row) -> set[str]:
    """Huellas distintas que consumen el límite de 5 (no eventos ni categorías).

    Una huella registrada como overflow nunca consumió el límite; su posible
    ``bi:postfinal`` contextual (K-H3) tampoco lo consume, por eso se resta.
    """
    keys = (
        IotMqttDeliveryEvent.objects.filter(delivery=row)
        .filter(
            Q(dedupe_key__startswith="bi:mismatch:")
            | Q(dedupe_key__startswith="bi:postfinal:")
            | Q(dedupe_key__startswith="bi:overflow:")
        )
        .values_list("dedupe_key", flat=True)
    )
    counted: set[str] = set()
    overflow: set[str] = set()
    for key in keys:
        _, kind, evidence = key.split(":", 2)
        (overflow if kind == "overflow" else counted).add(evidence)
    return counted - overflow


def _record_context(row, dedupe_key, incoming_class, incoming, seen) -> None:
    """Evidencia ya registrada (mismatch u overflow) que reaparece tras el estado
    final: evento contextual ``bi:postfinal`` que NO consume el límite (ver
    ``_counted_evidences``) ni modifica estado, conflicto ni overflow."""
    origin = "mismatch" if "mismatch" in seen else "overflow"
    _insert_once(
        row,
        dedupe_key,
        EVENT_POSTFINAL,
        incoming_class,
        f"contexto: evidencia ya registrada como {origin} reaparece tras el estado final "
        f"({short_fingerprint(incoming)})",
    )
    logger.error(
        "identidad de broker ya discrepante reaparece tras el estado final en %s (%s)",
        row.pk,
        row.state,
    )


def _insert_once(row, dedupe_key, event_type, result_code, detail) -> None:
    if _event_exists(row, dedupe_key):
        return
    try:
        with transaction.atomic():
            _create_event(row, dedupe_key, event_type, result_code, detail)
    except IntegrityError as exc:
        if not _is_dedupe_collision(exc, row, dedupe_key):
            raise


def _create_event(row, dedupe_key, event_type, result_code, detail) -> None:
    IotMqttDeliveryEvent.objects.create(
        delivery=row,
        event_type=event_type,
        from_state=row.state,
        to_state=row.state,
        result_code=result_code,
        correlation_id=row.id,
        safe_detail=detail[:1000],
        dedupe_key=dedupe_key,
    )


def _event_exists(row, dedupe_key) -> bool:
    return IotMqttDeliveryEvent.objects.filter(delivery=row, dedupe_key=dedupe_key).exists()


def _count_events(row, *prefixes: str) -> int:
    condition = Q()
    for prefix in prefixes:
        condition |= Q(dedupe_key__startswith=prefix)
    return IotMqttDeliveryEvent.objects.filter(delivery=row).filter(condition).count()


def _mysql_errno(exc: BaseException) -> int | None:
    """Código de error estructurado del driver (Django conserva ``args``)."""
    for candidate in (exc, getattr(exc, "__cause__", None)):
        args = getattr(candidate, "args", None)
        if args and isinstance(args[0], int):
            return args[0]
    return None


def _is_dedupe_collision(exc: IntegrityError, row, dedupe_key: str) -> bool:
    """H-03: solo una colisión real de ``iot_mqtt_event_dedupe_uniq``.

    Exige el código estructurado 1062 y, tras revertir el *savepoint*, que
    exista exactamente el evento ``(delivery, dedupe_key)``: no depende del
    texto (posiblemente localizado) del mensaje ni confunde otro índice.
    """
    if _mysql_errno(exc) != MYSQL_DUPLICATE_ENTRY:
        return False
    return _event_exists(row, dedupe_key)


# --------------------------------------------------------------------------
# Worker: veredicto en modo record (sin claves, sin token)
# --------------------------------------------------------------------------


class IdentityVerdictStatus(StrEnum):
    """J-02: estado explícito; ninguno distinto de MATCH equivale a verificado."""

    MATCH = "match"
    MISMATCH = "mismatch"
    UNVERIFIABLE = "unverifiable"
    NOT_OBSERVED = "not_observed"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True, slots=True)
class IdentityVerdict:
    status: IdentityVerdictStatus
    reason: str

    @property
    def verified(self) -> bool:
        return self.status is IdentityVerdictStatus.MATCH

    @property
    def would_reject(self) -> bool:
        """Lo que haría un futuro enforce (documentado, no implementado).

        NOT_APPLICABLE (no capturada por diseño: histórica u off) no es una
        identidad verificada; su tratamiento en enforce queda definido en la
        documentación (exenta solo antes de ENFORCE_SINCE).
        """
        return self.status in (
            IdentityVerdictStatus.MISMATCH,
            IdentityVerdictStatus.UNVERIFIABLE,
            IdentityVerdictStatus.NOT_OBSERVED,
        )


def _verdict(status: IdentityVerdictStatus, reason: str) -> IdentityVerdict:
    return IdentityVerdict(status=status, reason=reason)


def identity_verdict(delivery: IotMqttDelivery) -> IdentityVerdict:
    """Veredicto explícito sobre la fila (bloqueada por el worker). No rechaza nada."""
    presence = delivery.broker_identity_presence
    if presence is None:
        if observation_failed_at_receipt(delivery):
            return _verdict(IdentityVerdictStatus.NOT_OBSERVED, "not_observed")
        return _verdict(IdentityVerdictStatus.NOT_APPLICABLE, "not_captured")
    if delivery.broker_identity_conflict:
        return _verdict(IdentityVerdictStatus.UNVERIFIABLE, "ambiguous")
    if presence == BrokerIdentityPresence.ABSENT:
        return _verdict(IdentityVerdictStatus.UNVERIFIABLE, "missing")
    if presence in (BrokerIdentityPresence.MULTIPLE, BrokerIdentityPresence.OVERSIZED):
        return _verdict(IdentityVerdictStatus.UNVERIFIABLE, "ambiguous")
    if presence == BrokerIdentityPresence.INVALID:
        return _verdict(IdentityVerdictStatus.UNVERIFIABLE, "invalid")
    try:
        gateway_uid = parse_topic(delivery.topic)
    except TopicError:
        return _verdict(IdentityVerdictStatus.UNVERIFIABLE, "invalid_topic")
    identity = (
        BrokerIdentity.objects.filter(
            gateway__uid=gateway_uid,
            provider=PROVIDER_FLESPI,
            registered_at__lte=delivery.received_at,
        )
        .filter(Q(revoked_at__isnull=True) | Q(revoked_at__gt=delivery.received_at))
        .order_by("-registered_at", "-id")
        .first()
    )
    if identity is None:
        return _verdict(IdentityVerdictStatus.UNVERIFIABLE, "unregistered")
    expected = (
        BrokerIdentityFingerprint.objects.filter(
            identity=identity, key_version=delivery.broker_token_key_version
        )
        .values_list("fingerprint", flat=True)
        .first()
    )
    if expected is None:
        return _verdict(IdentityVerdictStatus.UNVERIFIABLE, "unregistered")
    if hmac.compare_digest(bytes(expected), bytes(delivery.broker_token_fingerprint)):
        return _verdict(IdentityVerdictStatus.MATCH, "match")
    return _verdict(IdentityVerdictStatus.MISMATCH, "mismatch")


def record_identity_verdict(
    delivery: IotMqttDelivery, *, worker_id: str | None
) -> IdentityVerdict | None:
    """Calcula el veredicto y, si enforce rechazaría, deja el evento. Nunca lanza
    por errores propios (solo propaga ``DatabaseError``) ni altera la ingesta."""
    try:
        verdict = identity_verdict(delivery)
    except DatabaseError:
        raise
    except Exception as exc:  # noqa: BLE001 - record nunca debe alterar la ingesta
        logger.error(
            "veredicto de identidad no calculado para %s (%s)", delivery.pk, type(exc).__name__
        )
        return None
    if not verdict.would_reject:
        return verdict
    reason = verdict.reason
    IotMqttDeliveryEvent.objects.create(
        delivery=delivery,
        event_type=EVENT_WOULD_REJECT,
        from_state=delivery.state,
        to_state=delivery.state,
        worker_id=worker_id,
        result_code=f"would_reject_{reason}",
        correlation_id=delivery.id,
        safe_detail="modo record: la medición no se rechaza por identidad del broker",
    )
    logger.warning(
        "identidad de broker: entrega %s se rechazaría (%s) en enforce", delivery.pk, reason
    )
    return verdict


# --------------------------------------------------------------------------
# Registro y rotación (comandos administrativos)
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RetirementReport:
    version: int
    is_loaded_as_current_or_next: bool
    nonfinal_deliveries: int
    active_identities_without_current: int

    @property
    def objective_criteria_met(self) -> bool:
        return (
            not self.is_loaded_as_current_or_next
            and self.nonfinal_deliveries == 0
            and self.active_identities_without_current == 0
        )


def _token_bytes(token_id: str) -> bytes:
    presence, value = classify_user_properties([("token_id", token_id)])
    if presence is not BrokerIdentityPresence.SINGLE:
        raise BrokerIdentityError(f"token_id no válido ({presence.value})")
    return value


def _validate_process_id(process_id: str) -> str:
    if not isinstance(process_id, str) or not process_id.strip() or len(process_id) > 100:
        raise BrokerIdentityError("identificador de proceso no válido")
    return process_id.strip()


def register_identity(
    *, gateway_uid: str, token_id: str, keyring: BrokerIdentityKeyRing, process_id: str
) -> BrokerIdentity:
    """Alta de la identidad activa de un gateway: huella vigente (+ NEXT)."""
    value = _token_bytes(token_id)
    process_id = _validate_process_id(process_id)
    require_mariadb(DEFAULT_DB_ALIAS)
    with transaction.atomic(using=DEFAULT_DB_ALIAS):
        gateway = Gateway.objects.select_for_update().filter(uid=gateway_uid).first()
        if gateway is None or not gateway.is_active:
            raise BrokerIdentityError("gateway no encontrado o inactivo")
        if BrokerIdentity.objects.filter(
            gateway=gateway, provider=PROVIDER_FLESPI, active_slot=ACTIVE_SLOT
        ).exists():
            raise BrokerIdentityError("el gateway ya tiene una identidad activa; revóquela antes")
        identity = BrokerIdentity.objects.create(
            gateway=gateway,
            provider=PROVIDER_FLESPI,
            registered_at=timezone.now(),
            registered_process_id=process_id,
        )
        versions = [keyring.current_version]
        if keyring.next_version is not None:
            versions.append(keyring.next_version)
        for version in versions:
            _create_fingerprint(
                identity, version, token_fingerprint(keyring.key_for(version), value), process_id
            )
        return identity


def register_next_fingerprint(
    *, gateway_uid: str, token_id: str, keyring: BrokerIdentityKeyRing, process_id: str
) -> BrokerIdentityFingerprint:
    """Rotación paso 3: huella en NEXT tras verificar el token contra la vigente."""
    if keyring.next_version is None:
        raise BrokerIdentityError("no hay versión NEXT configurada")
    value = _token_bytes(token_id)
    process_id = _validate_process_id(process_id)
    require_mariadb(DEFAULT_DB_ALIAS)
    with transaction.atomic(using=DEFAULT_DB_ALIAS):
        identity = (
            BrokerIdentity.objects.select_for_update()
            .filter(gateway__uid=gateway_uid, provider=PROVIDER_FLESPI, active_slot=ACTIVE_SLOT)
            .first()
        )
        if identity is None:
            raise BrokerIdentityError("el gateway no tiene identidad activa")
        current = identity.fingerprints.filter(key_version=keyring.current_version).first()
        if current is None:
            raise BrokerIdentityError("la identidad no tiene huella en la versión vigente")
        candidate = token_fingerprint(keyring.key_for(keyring.current_version), value)
        if not hmac.compare_digest(candidate, bytes(current.fingerprint)):
            raise BrokerIdentityError("el token no coincide con la identidad registrada")
        next_fp = token_fingerprint(keyring.key_for(keyring.next_version), value)
        existing = identity.fingerprints.filter(key_version=keyring.next_version).first()
        if existing is not None:
            if not hmac.compare_digest(next_fp, bytes(existing.fingerprint)):
                raise BrokerIdentityError("huella NEXT existente distinta")
            return existing
        return _create_fingerprint(identity, keyring.next_version, next_fp, process_id)


def revoke_identity(*, gateway_uid: str, cause: str, process_id: str) -> BrokerIdentity:
    cause = BrokerIdentityRevocationCause(cause)
    process_id = _validate_process_id(process_id)
    require_mariadb(DEFAULT_DB_ALIAS)
    with transaction.atomic(using=DEFAULT_DB_ALIAS):
        identity = (
            BrokerIdentity.objects.select_for_update()
            .filter(gateway__uid=gateway_uid, provider=PROVIDER_FLESPI, active_slot=ACTIVE_SLOT)
            .first()
        )
        if identity is None:
            raise BrokerIdentityError("el gateway no tiene identidad activa")
        identity.active_slot = None
        identity.revoked_at = timezone.now()
        identity.revoked_process_id = process_id
        identity.revocation_cause = cause
        identity.save(
            update_fields=["active_slot", "revoked_at", "revoked_process_id", "revocation_cause"]
        )
        return identity


def _create_fingerprint(identity, version, fingerprint, process_id) -> BrokerIdentityFingerprint:
    try:
        with transaction.atomic():
            return BrokerIdentityFingerprint.objects.create(
                identity=identity,
                provider=PROVIDER_FLESPI,
                key_version=version,
                fingerprint=fingerprint,
                created_process_id=process_id,
            )
    except IntegrityError as exc:
        if "iot_bidfp_provider_fp_uniq" in str(exc):
            raise BrokerIdentityError(
                "ese token ya está asociado a otra identidad (huella repetida)"
            ) from None
        raise


def active_identities_missing_version(version: int) -> list[str]:
    """Gateways con identidad activa sin huella en ``version`` (solo lectura)."""
    return list(
        BrokerIdentity.objects.filter(provider=PROVIDER_FLESPI, active_slot=ACTIVE_SLOT)
        .exclude(fingerprints__key_version=version)
        .order_by("gateway__uid")
        .values_list("gateway__uid", flat=True)
    )


def nonfinal_versions() -> dict[int, int]:
    """Entregas no finales por versión de clave de su evidencia (solo lectura)."""
    rows = (
        IotMqttDelivery.objects.filter(
            state__in=NON_FINAL_STATES, broker_token_key_version__isnull=False
        )
        .values("broker_token_key_version")
        .annotate(total=Count("id"))
    )
    return {row["broker_token_key_version"]: row["total"] for row in rows}


def retirement_report(version: int, keyring: BrokerIdentityKeyRing) -> RetirementReport:
    loaded = version in {keyring.current_version, keyring.next_version}
    return RetirementReport(
        version=version,
        is_loaded_as_current_or_next=loaded,
        nonfinal_deliveries=nonfinal_versions().get(version, 0),
        active_identities_without_current=len(
            active_identities_missing_version(keyring.current_version)
        ),
    )


__all__ = [
    "BrokerIdentityError",
    "MAX_IDENTITY_ANOMALIES",
    "RetirementReport",
    "active_identities_missing_version",
    "IdentityVerdict",
    "IdentityVerdictStatus",
    "identity_verdict",
    "observation_failed_at_receipt",
    "observe_redelivery_safely",
    "prepare_new_delivery_evidence",
    "record_observation_failure",
    "nonfinal_versions",
    "record_identity_verdict",
    "record_redelivery_identity",
    "register_identity",
    "register_next_fingerprint",
    "retirement_report",
    "revoke_identity",
]
