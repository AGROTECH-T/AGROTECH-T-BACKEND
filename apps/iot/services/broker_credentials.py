"""Credenciales de broker: registro, confirmación, rotación y revocación (FLESPI-01).

Propósito: ciclo de vida local de la credencial MQTT individual que un
proveedor externo (Flespi, ADR-FLESPI-00) emite para cada gateway. Django es la
autoridad del estado; estos servicios **no llaman a ningún proveedor real**
(no existe adaptador hasta FLESPI-03) ni revocan nada fuera de la base.

Estados y transiciones (``BrokerCredentialState``)::

    pending_broker --confirm (sin activa)------------------> active
    pending_broker --confirm (con activa): queda confirmada, sigue pendiente
    pending_broker --finalize_broker_rotation (evidencia)--> active
    pending_broker --revoke / cascada---------------------> revoked
    active         --revoke / finalize (rotated) / cascada-> revoked
    revoked: terminal; una credencial nueva es otra versión.

Reglas:

- Registrar, finalizar la rotación y revocar: administrador de una finca
  activa (``access.lock_manage_membership``: autorización -> MariaDB ->
  validación), como el resto de escrituras IoT.
- Confirmar: proceso backend. La confirmación **no es un argumento**: el
  servicio la pide al proveedor recibido, que debe ser una fuente confiable
  (``brokers.provider.source_origin``). En FLESPI-01 no hay proveedores de
  producción registrados, así que fuera de las pruebas ninguna credencial
  puede confirmarse. No hay ruta HTTP: el frontend no puede activar nada.
- Coexistencia: una pendiente y una activa por gateway y proveedor. Confirmar
  nunca revoca la activa. La rotación termina solo con
  ``finalize_broker_rotation``, que exige evidencia de un verificador
  confiable de que el gateway ya usa la credencial nueva; sin verificador
  registrado (FLESPI-04/05) la operación se rechaza, no se simula.
- La finca siempre sale del gateway; nunca de un dato del cliente.
- Orden de bloqueo: Farm -> FarmMembership -> Gateway -> credenciales de
  gateway -> credenciales de broker. El proceso omite FarmMembership y respeta
  el resto del orden.
- Nunca se guardan, auditan ni registran en logs el secreto, su huella, el
  ``token_id`` del proveedor ni la referencia de confirmación en logs.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone

from apps.accounts.errors import AppError
from apps.accounts.models import User
from apps.farms.models import Farm
from apps.iot import gateway_credentials as crypto
from apps.iot.brokers.provider import (
    PROVIDER_FLESPI,
    TRUSTED_EVIDENCE_VERIFIER_TYPES,
    TRUSTED_PROVIDER_TYPES,
    BrokerProviderError,
    ConfirmationOrigin,
    CredentialRequest,
    ProviderConfirmation,
    ProviderOutcome,
    ProviderStatusResult,
    RotationEvidence,
    UntrustedSourceError,
    source_origin,
)
from apps.iot.db_guard import require_mariadb
from apps.iot.identifiers import IdentifierKind
from apps.iot.models import (
    ACTIVE_SLOT,
    BROKER_PROCESS_ID_MAX_LENGTH,
    BrokerConfirmationOrigin,
    BrokerCredential,
    BrokerCredentialRevocationCause,
    BrokerCredentialState,
    Gateway,
)
from apps.iot.services._common import clean_reason, lock_by_uid, parse_uid, required_reason
from apps.iot.services.access import lock_manage_membership, require_read_membership
from apps.trazabilidad.models import EventType
from apps.trazabilidad.services import record_audit_event

logger = logging.getLogger(__name__)

ENTITY_TYPE = "iot_broker_credential"
PROVIDER = PROVIDER_FLESPI
CLOCK_SKEW = timedelta(minutes=5)

GATEWAY_NOT_FOUND = "gateway no encontrado"
GATEWAY_INACTIVE = "el gateway está desactivado"
FARM_INACTIVE = "la finca está desactivada"
CREDENTIAL_NOT_FOUND = "credencial de broker no encontrada"
ALREADY_PENDING = "el gateway ya tiene una credencial de broker pendiente"
NOT_PENDING = "la credencial de broker no está pendiente"
ALREADY_CONFIRMED = "la credencial de broker ya fue confirmada"
ALREADY_REVOKED = "la credencial de broker ya está revocada"
NOT_CONFIRMED_BY_PROVIDER = "el proveedor no confirmó la credencial"
INVALID_CONFIRMATION = "confirmación del proveedor no válida"
CONFIRMATION_REUSED = "la confirmación del proveedor ya fue utilizada"
PROVIDER_FAILURE = "fallo del proveedor de broker"
PROVIDER_UNAVAILABLE = "proveedor de broker no disponible"
UNTRUSTED_PROVIDER = "proveedor de broker no registrado como fuente confiable"
NO_ROTATION = "no hay una credencial de broker pendiente para finalizar"
ROTATION_NOT_CONFIRMED = "la credencial pendiente aún no está confirmada por el proveedor"
EVIDENCE_UNAVAILABLE = (
    "no hay un verificador de uso registrado; la rotación requiere evidencia "
    "de que el gateway usa la credencial nueva"
)
EVIDENCE_MISSING = "no hay evidencia de que el gateway use la credencial nueva"
EVIDENCE_INVALID = "evidencia de uso no válida"
EVIDENCE_REUSED = "la evidencia de uso ya fue utilizada"
INVALID_CAUSE = "causa no válida"
INVALID_PROCESS = "identificador de proceso no válido"
INVALID_VERSION = "versión no válida"

MANUAL_CAUSES = frozenset(
    {
        BrokerCredentialRevocationCause.ADMIN,
        BrokerCredentialRevocationCause.COMPROMISED,
        BrokerCredentialRevocationCause.ABANDONED,
    }
)
_PROCESS_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,99}\Z")


@dataclass(frozen=True)
class BrokerConfirmationResult:
    """Resultado de confirmar: ``activated`` es falso si coexiste con otra activa."""

    credential: BrokerCredential
    activated: bool
    origin: str


# --- Escritura: administrador -------------------------------------------------


def register_pending_broker_credential(
    *, user: User, farm_id: int, gateway_uid
) -> BrokerCredential:
    """Registra la versión siguiente en ``pending_broker`` (puede coexistir con la activa)."""
    with transaction.atomic():
        membership = lock_manage_membership(user=user, farm_id=farm_id)
        gateway = _lock_active_gateway(farm_id=farm_id, gateway_uid=gateway_uid)
        open_rows = _lock_open_rows(gateway)
        if any(row.is_pending for row in open_rows):
            raise AppError(ALREADY_PENDING, 409)
        latest = (
            BrokerCredential.objects.filter(gateway=gateway, provider=PROVIDER)
            .aggregate(latest=Max("version"))["latest"]
            or 0
        )
        try:
            with transaction.atomic():
                credential = BrokerCredential.objects.create(
                    gateway=gateway,
                    provider=PROVIDER,
                    version=latest + 1,
                    correlation_id=uuid4(),
                    state=BrokerCredentialState.PENDING_BROKER,
                    created_at=timezone.now(),
                    created_by=user,
                )
        except IntegrityError:
            raise AppError(ALREADY_PENDING, 409) from None
        _audit(
            farm=membership.farm,
            actor=user,
            event_type=EventType.IOT_BROKER_CREDENTIAL_REGISTERED,
            credential=credential,
            after=_snapshot(credential),
        )
        return credential


def finalize_broker_rotation(
    *, user: User, farm_id: int, gateway_uid, verifier, reason=None
) -> BrokerCredential:
    """Activa la pendiente confirmada y revoca la anterior, solo con evidencia.

    La evidencia la produce ``verifier`` (``RotationEvidenceVerifier``), que
    debe ser una fuente confiable. El administrador no puede declararla: sin
    verificador registrado o sin evidencia, se rechaza y la credencial anterior
    sigue activa. Si no hay credencial activa (p. ej. se revocó durante la
    rotación), activa la pendiente con el mismo requisito de evidencia.
    """
    with transaction.atomic():
        membership = lock_manage_membership(user=user, farm_id=farm_id)
        clean = required_reason(reason)
        try:
            evidence_origin = source_origin(verifier, TRUSTED_EVIDENCE_VERIFIER_TYPES)
        except UntrustedSourceError:
            raise AppError(EVIDENCE_UNAVAILABLE, 409) from None
        gateway = _lock_active_gateway(farm_id=farm_id, gateway_uid=gateway_uid)
        open_rows = _lock_open_rows(gateway)
        pending = next((row for row in open_rows if row.is_pending), None)
        active = next((row for row in open_rows if row.is_active), None)
        if pending is None:
            raise AppError(NO_ROTATION, 409)
        if not pending.is_confirmed:
            raise AppError(ROTATION_NOT_CONFIRMED, 409)
        evidence = _verified_evidence(verifier, gateway=gateway, pending=pending)
        now = timezone.now()
        previous = None
        if active is not None:
            previous = _snapshot(active)
            _revoke(active, now=now, user=user, cause=BrokerCredentialRevocationCause.ROTATED)
        before = _snapshot(pending)
        pending.state = BrokerCredentialState.ACTIVE
        pending.pending_slot = None
        pending.active_slot = ACTIVE_SLOT
        pending.activated_at = now
        pending.activated_by = user
        pending.activation_evidence_ref = evidence.evidence_ref
        try:
            with transaction.atomic():
                pending.save(
                    update_fields=[
                        "state",
                        "pending_slot",
                        "active_slot",
                        "activated_at",
                        "activated_by",
                        "activation_evidence_ref",
                    ]
                )
        except IntegrityError:
            raise AppError(EVIDENCE_REUSED, 409) from None
        if active is not None:
            _audit(
                farm=membership.farm,
                actor=user,
                event_type=EventType.IOT_BROKER_CREDENTIAL_REVOKED,
                credential=active,
                before=previous,
                after=_snapshot(active),
                reason=clean,
                metadata={
                    "cause": str(BrokerCredentialRevocationCause.ROTATED),
                    "replaced_by_bcred_id": pending.pk,
                },
            )
        _audit(
            farm=membership.farm,
            actor=user,
            event_type=EventType.IOT_BROKER_CREDENTIAL_ACTIVATED,
            credential=pending,
            before=before,
            after=_snapshot(pending),
            reason=clean,
            metadata={
                "mode": "rotation",
                "previous_bcred_id": active.pk if active is not None else None,
                "evidence_ref": str(evidence.evidence_ref),
                "evidence_method": evidence.method,
                "evidence_origin": str(evidence_origin),
            },
        )
        return pending


def revoke_broker_credential(
    *,
    user: User,
    farm_id: int,
    gateway_uid,
    version,
    cause=BrokerCredentialRevocationCause.ADMIN,
    reason=None,
) -> BrokerCredential:
    """Revocación local (``admin``, ``compromised`` o ``abandoned``). No llama al proveedor.

    ``abandoned`` solo para una credencial pendiente (rotación abandonada): la
    activa sigue intacta.
    """
    with transaction.atomic():
        membership = lock_manage_membership(user=user, farm_id=farm_id)
        if cause not in MANUAL_CAUSES:
            raise AppError(INVALID_CAUSE, 400)
        if type(version) is not int or version < 1:
            raise AppError(INVALID_VERSION, 400)
        clean = clean_reason(reason)
        uid = parse_uid(gateway_uid, IdentifierKind.GATEWAY, GATEWAY_NOT_FOUND)
        gateway = lock_by_uid(Gateway, farm_id=farm_id, uid=uid, not_found=GATEWAY_NOT_FOUND)
        credential = (
            BrokerCredential.objects.select_for_update()
            .filter(gateway=gateway, provider=PROVIDER, version=version)
            .first()
        )
        if credential is None:
            raise AppError(CREDENTIAL_NOT_FOUND, 404)
        if credential.is_revoked:
            raise AppError(ALREADY_REVOKED, 409)
        if cause == BrokerCredentialRevocationCause.ABANDONED and not credential.is_pending:
            raise AppError(INVALID_CAUSE, 400)
        before = _snapshot(credential)
        _revoke(credential, now=timezone.now(), user=user, cause=cause)
        _audit(
            farm=membership.farm,
            actor=user,
            event_type=EventType.IOT_BROKER_CREDENTIAL_REVOKED,
            credential=credential,
            before=before,
            after=_snapshot(credential),
            reason=clean,
            metadata={"cause": str(cause)},
        )
        return credential


def revoke_broker_credentials_for_deactivated_gateway(
    *, membership, user, gateway, now, reason=""
) -> int:
    """Revoca la pendiente y la activa de un gateway ya bloqueado (uso interno).

    La invoca ``devices.deactivate_gateway`` dentro de su transacción, después
    de las credenciales de gateway. No llama al proveedor (FLESPI-05).
    """
    revoked = 0
    for credential in _lock_open_rows(gateway):
        before = _snapshot(credential)
        _revoke(
            credential,
            now=now,
            user=user,
            cause=BrokerCredentialRevocationCause.GATEWAY_DEACTIVATED,
        )
        _audit(
            farm=membership.farm,
            actor=user,
            event_type=EventType.IOT_BROKER_CREDENTIAL_REVOKED,
            credential=credential,
            before=before,
            after=_snapshot(credential),
            reason=reason,
            metadata={"cause": str(BrokerCredentialRevocationCause.GATEWAY_DEACTIVATED)},
        )
        revoked += 1
    return revoked


# --- Escritura: proceso backend ----------------------------------------------


def confirm_broker_credential(
    *, provider, gateway_uid, correlation_id, process_id
) -> BrokerConfirmationResult:
    """Pide al proveedor el estado de la credencial pendiente y registra la confirmación.

    Sin credencial activa, la confirmación la activa (primera credencial).
    Con una activa, la pendiente queda confirmada y la activa no cambia: la
    rotación termina con ``finalize_broker_rotation``.
    """
    process = _validate_process_id(process_id)
    try:
        origin = source_origin(provider, TRUSTED_PROVIDER_TYPES)
    except UntrustedSourceError:
        raise AppError(UNTRUSTED_PROVIDER, 403) from None
    if getattr(provider, "provider_name", None) != PROVIDER:
        raise AppError(UNTRUSTED_PROVIDER, 403)
    uid = parse_uid(gateway_uid, IdentifierKind.GATEWAY, GATEWAY_NOT_FOUND)
    if not isinstance(correlation_id, UUID):
        raise AppError(CREDENTIAL_NOT_FOUND, 404)
    require_mariadb()
    snapshot = (
        BrokerCredential.objects.filter(
            gateway__uid=uid, provider=PROVIDER, correlation_id=correlation_id
        )
        .only("id", "version", "state", "confirmation_ref")
        .first()
    )
    if snapshot is None:
        raise AppError(CREDENTIAL_NOT_FOUND, 404)
    if not snapshot.is_pending:
        raise AppError(NOT_PENDING, 409)
    if snapshot.is_confirmed:
        raise AppError(ALREADY_CONFIRMED, 409)
    # Consulta al proveedor fuera de la transacción y sin bloqueos tomados.
    status = _query_provider(
        provider,
        CredentialRequest(gateway_uid=uid, correlation_id=correlation_id, version=snapshot.version),
    )
    if status.outcome is not ProviderOutcome.ENABLED:
        raise AppError(NOT_CONFIRMED_BY_PROVIDER, 409)
    confirmation = status.confirmation

    with transaction.atomic():
        gateway_row = Gateway.objects.filter(uid=uid).values_list("pk", "farm_id").first()
        if gateway_row is None:
            raise AppError(GATEWAY_NOT_FOUND, 404)
        gateway_id, farm_id = gateway_row
        farm = Farm.objects.select_for_update().filter(pk=farm_id).first()
        if farm is None or not farm.is_active:
            raise AppError(FARM_INACTIVE, 409)
        gateway = Gateway.objects.select_for_update().filter(pk=gateway_id, farm_id=farm_id).first()
        if gateway is None or gateway.uid != uid:
            raise AppError(GATEWAY_NOT_FOUND, 404)
        if not gateway.is_active:
            raise AppError(GATEWAY_INACTIVE, 409)
        open_rows = _lock_open_rows(gateway)
        credential = next(
            (row for row in open_rows if row.correlation_id == correlation_id), None
        )
        if credential is None or not credential.is_pending:
            raise AppError(NOT_PENDING, 409)
        if credential.is_confirmed:
            raise AppError(ALREADY_CONFIRMED, 409)
        _validate_confirmation(confirmation, credential=credential)
        if BrokerCredential.objects.filter(confirmation_ref=confirmation.confirmation_id).exists():
            raise AppError(CONFIRMATION_REUSED, 409)
        active = next((row for row in open_rows if row.is_active), None)
        now = timezone.now()
        before = _snapshot(credential)
        credential.confirmation_ref = confirmation.confirmation_id
        credential.confirmation_origin = _model_origin(origin)
        credential.confirmed_at = now
        credential.confirmed_process_id = process
        credential.secret_fingerprint = confirmation.secret_fingerprint
        credential.key_version = confirmation.key_version
        fields = [
            "confirmation_ref",
            "confirmation_origin",
            "confirmed_at",
            "confirmed_process_id",
            "secret_fingerprint",
            "key_version",
        ]
        activated = active is None
        if activated:
            credential.state = BrokerCredentialState.ACTIVE
            credential.pending_slot = None
            credential.active_slot = ACTIVE_SLOT
            credential.activated_at = now
            credential.activated_process_id = process
            fields += [
                "state",
                "pending_slot",
                "active_slot",
                "activated_at",
                "activated_process_id",
            ]
        try:
            with transaction.atomic():
                credential.save(update_fields=fields)
        except IntegrityError:
            # Huella o confirmación ya asociadas a otra credencial.
            raise AppError(CONFIRMATION_REUSED, 409) from None
        confirmed = _snapshot(credential)
        _audit(
            farm=farm,
            actor=None,
            event_type=EventType.IOT_BROKER_CREDENTIAL_CONFIRMED,
            credential=credential,
            before=before,
            after=confirmed,
            metadata={
                "process_id": process,
                "origin": str(origin),
                "confirmation_ref": str(confirmation.confirmation_id),
                "activated": activated,
            },
        )
        if activated:
            _audit(
                farm=farm,
                actor=None,
                event_type=EventType.IOT_BROKER_CREDENTIAL_ACTIVATED,
                credential=credential,
                before=before,
                after=confirmed,
                metadata={"mode": "initial", "process_id": process},
            )
        logger.info(
            "credencial de broker %s v%s confirmada (activada=%s)",
            credential.pk,
            credential.version,
            activated,
        )
        return BrokerConfirmationResult(
            credential=credential, activated=activated, origin=str(origin)
        )


# --- Lectura -----------------------------------------------------------------


def get_active_broker_credential(*, user: User, farm_id: int, gateway_uid):
    """Credencial activa del gateway de la finca, o ``None`` (sin secretos)."""
    gateway = _read_gateway(user=user, farm_id=farm_id, gateway_uid=gateway_uid)
    return BrokerCredential.objects.filter(
        gateway=gateway, provider=PROVIDER, state=BrokerCredentialState.ACTIVE
    ).first()


def list_broker_credentials(*, user: User, farm_id: int, gateway_uid):
    """Historial completo de versiones del gateway de la finca (sin secretos)."""
    gateway = _read_gateway(user=user, farm_id=farm_id, gateway_uid=gateway_uid)
    return BrokerCredential.objects.filter(gateway=gateway, provider=PROVIDER)


# --- Internos ----------------------------------------------------------------


def _read_gateway(*, user, farm_id, gateway_uid) -> Gateway:
    require_read_membership(user=user, farm_id=farm_id)
    uid = parse_uid(gateway_uid, IdentifierKind.GATEWAY, GATEWAY_NOT_FOUND)
    gateway = Gateway.objects.filter(farm_id=farm_id, uid=uid).first()
    if gateway is None:
        raise AppError(GATEWAY_NOT_FOUND, 404)
    return gateway


def _lock_active_gateway(*, farm_id: int, gateway_uid) -> Gateway:
    uid = parse_uid(gateway_uid, IdentifierKind.GATEWAY, GATEWAY_NOT_FOUND)
    gateway = lock_by_uid(Gateway, farm_id=farm_id, uid=uid, not_found=GATEWAY_NOT_FOUND)
    if not gateway.is_active:
        raise AppError(GATEWAY_INACTIVE, 409)
    return gateway


def _lock_open_rows(gateway: Gateway) -> list[BrokerCredential]:
    """Bloquea la pendiente y la activa (en orden de ``id``) tras el gateway."""
    return list(
        BrokerCredential.objects.select_for_update()
        .filter(gateway=gateway, provider=PROVIDER)
        .exclude(state=BrokerCredentialState.REVOKED)
        .order_by("id")
    )


def _revoke(credential: BrokerCredential, *, now, user, cause) -> None:
    credential.state = BrokerCredentialState.REVOKED
    credential.pending_slot = None
    credential.active_slot = None
    credential.revoked_at = now
    credential.revoked_by = user
    credential.revocation_cause = str(cause)
    credential.save(
        update_fields=[
            "state",
            "pending_slot",
            "active_slot",
            "revoked_at",
            "revoked_by",
            "revocation_cause",
        ]
    )


def _validate_process_id(process_id) -> str:
    if not isinstance(process_id, str):
        raise AppError(INVALID_PROCESS, 400)
    value = process_id.strip()
    if len(value) > BROKER_PROCESS_ID_MAX_LENGTH or not _PROCESS_ID_RE.match(value):
        raise AppError(INVALID_PROCESS, 400)
    return value


def _query_provider(provider, request: CredentialRequest) -> ProviderStatusResult:
    try:
        status = provider.get_credential_status(request)
    except BrokerProviderError as exc:
        logger.warning("consulta al proveedor de broker fallida (%s)", type(exc).__name__)
        if exc.retryable:
            raise AppError(PROVIDER_UNAVAILABLE, 503) from None
        raise AppError(PROVIDER_FAILURE, 502) from None
    except Exception as exc:  # noqa: BLE001 - frontera con código del adaptador
        logger.error("respuesta inesperada del proveedor de broker (%s)", type(exc).__name__)
        raise AppError(PROVIDER_FAILURE, 502) from None
    if type(status) is not ProviderStatusResult or status.correlation_id != request.correlation_id:
        raise AppError(INVALID_CONFIRMATION, 502)
    return status


def _validate_confirmation(confirmation, *, credential: BrokerCredential) -> None:
    if type(confirmation) is not ProviderConfirmation:
        raise AppError(INVALID_CONFIRMATION, 502)
    now = timezone.now()
    if (
        confirmation.provider != credential.provider
        or confirmation.correlation_id != credential.correlation_id
        or confirmation.outcome is not ProviderOutcome.ENABLED
        or confirmation.key_version not in crypto.SUPPORTED_KEY_VERSIONS
        or not crypto.is_well_formed_fingerprint(confirmation.secret_fingerprint)
        or confirmation.observed_at < credential.created_at
        or confirmation.observed_at > now + CLOCK_SKEW
    ):
        raise AppError(INVALID_CONFIRMATION, 502)


def _verified_evidence(
    verifier, *, gateway: Gateway, pending: BrokerCredential
) -> RotationEvidence:
    try:
        evidence = verifier.verify_gateway_uses(
            gateway_uid=gateway.uid, correlation_id=pending.correlation_id
        )
    except Exception as exc:  # noqa: BLE001 - frontera con el verificador
        logger.error("verificación de uso de credencial fallida (%s)", type(exc).__name__)
        raise AppError(EVIDENCE_MISSING, 409) from None
    if evidence is None:
        raise AppError(EVIDENCE_MISSING, 409)
    now = timezone.now()
    if (
        type(evidence) is not RotationEvidence
        or evidence.gateway_uid != gateway.uid
        or evidence.correlation_id != pending.correlation_id
        or evidence.observed_at < pending.confirmed_at
        or evidence.observed_at > now + CLOCK_SKEW
    ):
        raise AppError(EVIDENCE_INVALID, 409)
    if BrokerCredential.objects.filter(activation_evidence_ref=evidence.evidence_ref).exists():
        raise AppError(EVIDENCE_REUSED, 409)
    return evidence


def _model_origin(origin: ConfirmationOrigin) -> str:
    return BrokerConfirmationOrigin(str(origin)).value


def _snapshot(credential: BrokerCredential) -> dict:
    """Datos auditables: nunca el secreto, la huella ni el ``token_id``.

    Las claves evitan ``credential``, ``token``, ``secret`` y ``session``:
    ``record_audit_event`` rechaza cualquier clave que los contenga.
    """
    return {
        "bcred_id": credential.pk,
        "gateway_uid": credential.gateway.uid,
        "provider": credential.provider,
        "version": credential.version,
        "state": credential.state,
        "confirmed": credential.is_confirmed,
        "revocation_cause": credential.revocation_cause,
    }


def _audit(
    *, farm, actor, event_type, credential, before=None, after=None, reason="", metadata=None
) -> None:
    record_audit_event(
        farm=farm,
        actor=actor,
        event_type=event_type,
        entity_type=ENTITY_TYPE,
        entity_id=credential.pk,
        before=before,
        after=after,
        reason=reason,
        metadata=metadata,
    )


__all__ = [
    "BrokerConfirmationResult",
    "confirm_broker_credential",
    "finalize_broker_rotation",
    "get_active_broker_credential",
    "list_broker_credentials",
    "register_pending_broker_credential",
    "revoke_broker_credential",
    "revoke_broker_credentials_for_deactivated_gateway",
]
