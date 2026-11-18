"""Credenciales de gateway: emisión, rotación, revocación y verificación (IoT-03.1).

Propósito: ciclo de vida de la credencial con la que un gateway se autenticará
ante el broker MQTT (DEC-13 §12). Servicios internos, sin HTTP ni MQTT.

Reglas:
- Emitir, rotar y revocar: solo el administrador de una finca activa
  (``access.lock_manage_membership``: autorización → MariaDB → validación).
- Orden de bloqueo: Farm → FarmMembership → Gateway → credenciales.
- Una sola credencial vigente por gateway (``UNIQUE (gateway, active_slot)``).
- Rotar revoca la anterior y emite la nueva en la misma transacción, sin
  ventana de coexistencia; si algo falla, todo se revierte y la anterior sigue
  vigente.
- Una credencial revocada nunca vuelve a activarse: se emite otra.
- Desactivar el gateway revoca su credencial (``revoke_for_deactivated_gateway``,
  invocado por ``devices.deactivate_gateway``).
- Desactivar la finca no revoca: la verificación la rechaza mientras la finca
  esté inactiva y vuelve a aceptarla si se reactiva.
- El secreto solo se devuelve una vez, en ``IssuedGatewayCredential``. No se
  persiste, ni se audita, ni aparece en ``repr``, logs o excepciones; la huella
  tampoco se audita.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables

from apps.accounts.errors import AppError
from apps.accounts.models import User
from apps.iot import gateway_credentials as crypto
from apps.iot.identifiers import IdentifierKind, is_valid_identifier
from apps.iot.models import ACTIVE_SLOT, Gateway, GatewayCredential, RevocationCause
from apps.iot.services._common import audit, clean_reason, lock_by_uid, parse_uid
from apps.iot.services.access import lock_manage_membership, require_read_membership
from apps.trazabilidad.models import EventType

logger = logging.getLogger(__name__)

ENTITY_TYPE = "iot_gateway_credential"
GATEWAY_NOT_FOUND = "gateway no encontrado"
CREDENTIAL_NOT_FOUND = "el gateway no tiene una credencial activa"
ALREADY_HAS_CREDENTIAL = "el gateway ya tiene una credencial activa; rótela para reemplazarla"
GATEWAY_INACTIVE = "el gateway está desactivado"
INVALID_CAUSE = "causa no válida"

ROTATION_CAUSES = frozenset({RevocationCause.ROTATED, RevocationCause.REISSUED})
REVOCATION_EVENTS = {
    RevocationCause.ADMIN: EventType.IOT_GATEWAY_CREDENTIAL_REVOKED,
    RevocationCause.COMPROMISED: EventType.IOT_GATEWAY_CREDENTIAL_COMPROMISED,
}
# Valores fijos para igualar el coste cuando no hay credencial que comparar.
_DUMMY_UID = "GW-AAAAAAAAAA"
_DUMMY_SECRET = crypto.SECRET_PREFIX + "A" * crypto.SECRET_RANDOM_LENGTH


@dataclass(frozen=True)
class IssuedGatewayCredential:
    """Resultado de emitir o rotar: la credencial y su secreto, una sola vez."""

    credential: GatewayCredential
    secret: str = field(repr=False)


@dataclass(frozen=True)
class VerifiedGateway:
    """Identidad resuelta desde el gateway registrado, nunca desde el mensaje."""

    gateway_id: int
    gateway_uid: str
    farm_id: int


# --- Escritura ---


def issue_gateway_credential(*, user: User, farm_id: int, gateway_uid) -> IssuedGatewayCredential:
    """Emite la primera credencial de un gateway activo sin credencial vigente."""
    with transaction.atomic():
        membership = lock_manage_membership(user=user, farm_id=farm_id)
        gateway = _lock_active_gateway(farm_id=farm_id, gateway_uid=gateway_uid)
        if _lock_active_credential(gateway) is not None:
            raise AppError(ALREADY_HAS_CREDENTIAL, 409)
        issued = _create(gateway=gateway, user=user, now=timezone.now())
        audit(
            membership=membership,
            user=user,
            event_type=EventType.IOT_GATEWAY_CREDENTIAL_ISSUED,
            entity_type=ENTITY_TYPE,
            entity_id=issued.credential.pk,
            after=_snapshot(issued.credential),
        )
        return issued


def rotate_gateway_credential(
    *, user: User, farm_id: int, gateway_uid, cause=RevocationCause.ROTATED, reason=None
) -> IssuedGatewayCredential:
    """Revoca la credencial vigente y emite otra en la misma transacción.

    ``cause``: ``rotated`` (rotación ordinaria) o ``reissued`` (secreto
    perdido). Sin ventana de coexistencia.
    """
    with transaction.atomic():
        membership = lock_manage_membership(user=user, farm_id=farm_id)
        if cause not in ROTATION_CAUSES:
            raise AppError(INVALID_CAUSE, 400)
        clean = clean_reason(reason)
        gateway = _lock_active_gateway(farm_id=farm_id, gateway_uid=gateway_uid)
        current = _lock_active_credential(gateway)
        if current is None:
            raise AppError(CREDENTIAL_NOT_FOUND, 404)
        now = timezone.now()
        before = _snapshot(current)
        _revoke(current, user=user, now=now, cause=cause)
        issued = _create(gateway=gateway, user=user, now=now)
        audit(
            membership=membership,
            user=user,
            event_type=EventType.IOT_GATEWAY_CREDENTIAL_ROTATED,
            entity_type=ENTITY_TYPE,
            entity_id=issued.credential.pk,
            before=before,
            after=_snapshot(issued.credential),
            reason=clean,
            metadata={"cause": str(cause), "previous_gwcred_id": current.pk},
        )
        return issued


def revoke_gateway_credential(
    *, user: User, farm_id: int, gateway_uid, cause=RevocationCause.ADMIN, reason=None
) -> GatewayCredential:
    """Revoca la credencial vigente (``admin`` o ``compromised``)."""
    with transaction.atomic():
        membership = lock_manage_membership(user=user, farm_id=farm_id)
        if cause not in REVOCATION_EVENTS:
            raise AppError(INVALID_CAUSE, 400)
        clean = clean_reason(reason)
        uid = parse_uid(gateway_uid, IdentifierKind.GATEWAY, GATEWAY_NOT_FOUND)
        gateway = lock_by_uid(Gateway, farm_id=farm_id, uid=uid, not_found=GATEWAY_NOT_FOUND)
        current = _lock_active_credential(gateway)
        if current is None:
            raise AppError(CREDENTIAL_NOT_FOUND, 404)
        before = _snapshot(current)
        _revoke(current, user=user, now=timezone.now(), cause=cause)
        audit(
            membership=membership,
            user=user,
            event_type=REVOCATION_EVENTS[cause],
            entity_type=ENTITY_TYPE,
            entity_id=current.pk,
            before=before,
            after=_snapshot(current),
            reason=clean,
            metadata={"cause": str(cause)},
        )
        return current


def revoke_for_deactivated_gateway(*, membership, user, gateway, now, reason="") -> int:
    """Revoca la credencial vigente de un gateway ya bloqueado (uso interno).

    La invoca ``devices.deactivate_gateway`` dentro de su transacción.
    """
    current = _lock_active_credential(gateway)
    if current is None:
        return 0
    before = _snapshot(current)
    _revoke(current, user=user, now=now, cause=RevocationCause.GATEWAY_DEACTIVATED)
    audit(
        membership=membership,
        user=user,
        event_type=EventType.IOT_GATEWAY_CREDENTIAL_CASCADE_REVOKED,
        entity_type=ENTITY_TYPE,
        entity_id=current.pk,
        before=before,
        after=_snapshot(current),
        reason=reason,
        metadata={"cause": str(RevocationCause.GATEWAY_DEACTIVATED)},
    )
    return 1


# --- Lectura ---


def list_gateway_credentials(*, user: User, farm_id: int, gateway_uid):
    """Historial de credenciales de un gateway de la finca (sin secretos)."""
    require_read_membership(user=user, farm_id=farm_id)
    uid = parse_uid(gateway_uid, IdentifierKind.GATEWAY, GATEWAY_NOT_FOUND)
    gateway = Gateway.objects.filter(farm_id=farm_id, uid=uid).first()
    if gateway is None:
        raise AppError(GATEWAY_NOT_FOUND, 404)
    return GatewayCredential.objects.filter(gateway=gateway)


@sensitive_variables("secret")
def verify_gateway_credential(gateway_uid, secret) -> VerifiedGateway | None:
    """Verifica el ``uid`` y el secreto que presenta un gateway.

    Devuelve la identidad resuelta desde el gateway registrado o ``None``. No
    distingue el motivo del rechazo (``uid`` inexistente, secreto incorrecto,
    credencial revocada, gateway o finca inactivos) y calcula siempre una huella
    para no revelarlo por el tiempo de respuesta. No escribe nada (sin
    ``last_used_at``, IoT-05). La falta de clave se propaga
    (``ImproperlyConfigured``).
    """
    if not is_valid_identifier(gateway_uid, IdentifierKind.GATEWAY) or not (
        crypto.is_well_formed_secret(secret)
    ):
        crypto.fingerprint(_DUMMY_UID, _DUMMY_SECRET)
        return None
    credential = (
        GatewayCredential.objects.select_related("gateway__farm")
        .filter(gateway__uid=gateway_uid, active_slot=ACTIVE_SLOT)
        .first()
    )
    if credential is None:
        crypto.fingerprint(_DUMMY_UID, _DUMMY_SECRET)
        return None
    if credential.key_version not in crypto.SUPPORTED_KEY_VERSIONS:
        logger.warning(
            "credencial de gateway %s con versión de clave no soportada", credential.pk
        )
    matches = crypto.verify_secret(
        gateway_uid,
        secret,
        expected_fingerprint=credential.fingerprint,
        key_version=credential.key_version,
    )
    gateway = credential.gateway
    if not (matches and gateway.is_active and gateway.farm.is_active):
        return None
    return VerifiedGateway(gateway_id=gateway.pk, gateway_uid=gateway.uid, farm_id=gateway.farm_id)


# --- Internos ---


def _lock_active_gateway(*, farm_id: int, gateway_uid) -> Gateway:
    uid = parse_uid(gateway_uid, IdentifierKind.GATEWAY, GATEWAY_NOT_FOUND)
    gateway = lock_by_uid(Gateway, farm_id=farm_id, uid=uid, not_found=GATEWAY_NOT_FOUND)
    if not gateway.is_active:
        raise AppError(GATEWAY_INACTIVE, 409)
    return gateway


def _lock_active_credential(gateway: Gateway) -> GatewayCredential | None:
    return (
        GatewayCredential.objects.select_for_update()
        .filter(gateway=gateway, active_slot=ACTIVE_SLOT)
        .first()
    )


@sensitive_variables("secret", "fingerprint")
def _create(*, gateway: Gateway, user, now) -> IssuedGatewayCredential:
    secret, fingerprint, key_version = crypto.new_credential_for(gateway.uid)
    try:
        with transaction.atomic():
            credential = GatewayCredential.objects.create(
                gateway=gateway,
                fingerprint=fingerprint,
                key_version=key_version,
                active_slot=ACTIVE_SLOT,
                issued_at=now,
                issued_by=user,
            )
    except IntegrityError:
        raise AppError(ALREADY_HAS_CREDENTIAL, 409) from None
    return IssuedGatewayCredential(credential=credential, secret=secret)


def _revoke(credential: GatewayCredential, *, user, now, cause) -> None:
    credential.active_slot = None
    credential.revoked_at = now
    credential.revoked_by = user
    credential.revocation_cause = str(cause)
    credential.save(update_fields=["active_slot", "revoked_at", "revoked_by", "revocation_cause"])


def _snapshot(credential: GatewayCredential) -> dict:
    """Datos auditables: nunca el secreto ni la huella.

    Las claves no contienen ``credential``: ``record_audit_event`` rechaza todo
    nombre que contenga un término sensible, y esa protección no se relaja.
    """
    return {
        "gwcred_id": credential.pk,
        "gateway_uid": credential.gateway.uid,
        "key_version": credential.key_version,
        "active": credential.is_active,
        "revocation_cause": credential.revocation_cause,
    }
