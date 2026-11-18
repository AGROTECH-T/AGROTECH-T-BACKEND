"""Credenciales de coordinador: emisión, rotación, revocación y autenticación.

IoT-03.2 (DEC-13 §13). Servicios internos, sin HTTP ni MQTT.

Reglas:
- Emitir, rotar y revocar: solo el administrador de una finca activa
  (``access.lock_manage_membership``: autorización → MariaDB → validación).
- Orden de bloqueo: Farm → FarmMembership → Coordinator → credenciales y
  dependencias.
- Clave del coordinador: 32 bytes aleatorios, cifrados con AES-256-GCM antes de
  persistir. Solo se devuelve una vez, en ``IssuedCoordinatorCredential``; no se
  persiste, no se audita, no se registra y no aparece en ``repr``.
- Rotación inmediata: la credencial anterior se revoca en la misma transacción
  que emite la nueva; no hay periodo de coexistencia.
- Una credencial revocada nunca vuelve a activarse: se emite otra.
- Desactivar el coordinador revoca todas sus credenciales activas
  (``revoke_for_deactivated_coordinator``, invocado por
  ``devices.deactivate_coordinator``). Reactivar no las restaura.
- Autenticación de trama: recibe un ``ParsedCoordinatorFrame`` producido por el
  parser binario v1 (``apps.iot.lora_frame``, IoT-04.2) y exige el contexto HMAC
  como argumento; para la trama v1 es ``LORA_FRAME_HMAC_CONTEXT``.
- ``master_key_version`` identifica la versión AES-GCM de la clave maestra. La
  ``key_version`` de la trama LoRa es la versión declarada por el dispositivo:
  son conceptos distintos y no se mezclan.
- Reemisión (causa ``reissued``): además de revocar la clave anterior, reinicia
  la ventana anti-replay del coordinador en la misma transacción, para que un
  dispositivo reemplazado pueda volver a ``boot_counter`` 0.
- Anti-replay: ventana de cuatro arranques recientes. No decide nada sobre
  ``seq``: la identidad única aprobada
  ``(coordinator_id, credential_id, boot_counter, seq)`` y la transacción
  conjunta con la medición son responsabilidad de la futura ingestión (ver
  ``accept_coordinator_sequence``); este módulo no las implementa.
"""
from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables

from apps.accounts.errors import AppError
from apps.accounts.models import User
from apps.iot import coordinator_credentials as crypto
from apps.iot.identifiers import IdentifierKind
from apps.iot.models import (
    ACTIVE_SLOT,
    BOOT_WINDOW_SIZE,
    COORDINATOR_KEY_VERSION_MAX,
    COORDINATOR_KEY_VERSION_MIN,
    Coordinator,
    CoordinatorBootWindow,
    CoordinatorCredential,
    CoordinatorKeyGeneration,
    CoordinatorRevocationCause,
)
from apps.iot.services._common import audit, clean_reason, lock_by_uid, parse_uid
from apps.iot.services.access import lock_manage_membership, require_read_membership
from apps.trazabilidad.models import EventType
from config.coordinator_key_encryption_key import load_master_key_ring

logger = logging.getLogger(__name__)

ENTITY_TYPE = "iot_coordinator_credential"
COORDINATOR_NOT_FOUND = "coordinador no encontrado"
CREDENTIAL_NOT_FOUND = "el coordinador no tiene una credencial activa"
ALREADY_HAS_CREDENTIAL = (
    "el coordinador ya tiene una credencial activa; rótela para reemplazarla"
)
COORDINATOR_INACTIVE = "el coordinador está desactivado"
INVALID_CAUSE = "causa no válida"
NOT_IN_TRANSACTION = "accept_coordinator_sequence exige una transacción del llamador"
LEGACY_GENERATION_UNRESOLVED = (
    "el coordinador tiene credenciales sin generación; requiere regularización administrativa"
)
GENERATION_INCONSISTENT = "el contador de generaciones no coincide con las credenciales"
GENERATION_EXHAUSTED = "se agotó el rango de generaciones de credencial"

ROTATION_CAUSES = frozenset(
    {CoordinatorRevocationCause.ROTATED, CoordinatorRevocationCause.REISSUED}
)
REVOCATION_EVENTS = {
    CoordinatorRevocationCause.ADMIN: EventType.IOT_COORDINATOR_CREDENTIAL_REVOKED,
    CoordinatorRevocationCause.COMPROMISED: EventType.IOT_COORDINATOR_CREDENTIAL_COMPROMISED,
}
# Clave fija solo para igualar el coste cuando no hay credencial que verificar.
# No protege nada: se usa con datos que ya se van a rechazar.
_DUMMY_KEY = bytes(crypto.COORDINATOR_KEY_BYTES)


class SequenceOutOfWindow(Exception):
    """El ``boot_counter`` salió de la ventana de arranques recientes."""


class LegacyGenerationUnresolved(AppError):
    """Hay credenciales legacy sin generación y no existe contador durable."""

    def __init__(self):
        super().__init__(LEGACY_GENERATION_UNRESOLVED, 409)


class CoordinatorGenerationInconsistent(AppError):
    """El contador no coincide con la credencial vigente o con el historial."""

    def __init__(self):
        super().__init__(GENERATION_INCONSISTENT, 409)


class CoordinatorGenerationExhausted(AppError):
    """El contador llegó al máximo uint16 y no puede emitir otra generación."""

    def __init__(self):
        super().__init__(GENERATION_EXHAUSTED, 409)


@dataclass(frozen=True)
class IssuedCoordinatorCredential:
    """Resultado de emitir o rotar: la credencial y su clave, una sola vez."""

    credential: CoordinatorCredential
    key: bytes = field(repr=False)

    @sensitive_variables()
    def provisioning_key(self) -> str:
        """Clave en Base64 para la carga física en el coordinador (una vez)."""
        return base64.b64encode(self.key).decode("ascii")


@dataclass(frozen=True)
class AuthenticatedCoordinatorFrame:
    """Trama autenticada; la finca sale del coordinador registrado.

    Aunque el objeto es público y puede construirse directamente, el servicio
    ``accept_coordinator_sequence`` revalida ``coordinator_id``,
    ``coordinator_uid``, ``farm_id`` y ``credential_id`` contra la base antes de
    tocar la ventana. ``key_version`` es la generación de la credencial ya
    validada contra la base; nunca la versión de la clave maestra AES-GCM.
    """

    coordinator_id: int
    coordinator_uid: str
    farm_id: int
    credential_id: int
    boot_counter: int
    seq: int
    key_version: int


# --- Escritura ---


def issue_coordinator_credential(
    *, user: User, farm_id: int, coordinator_uid
) -> IssuedCoordinatorCredential:
    """Emite la primera credencial de un coordinador activo sin credencial."""
    issued = None
    exhausted = False
    with transaction.atomic():
        membership = lock_manage_membership(user=user, farm_id=farm_id)
        coordinator = _lock_active_coordinator(farm_id=farm_id, coordinator_uid=coordinator_uid)
        if _lock_active_credential(coordinator) is not None:
            raise AppError(ALREADY_HAS_CREDENTIAL, 409)
        now = timezone.now()
        generation = _lock_generation(coordinator)
        key_version, generation, exhausted = _allocate_generation(
            coordinator=coordinator,
            generation=generation,
            now=now,
            membership=membership,
            user=user,
            entity_id=coordinator.pk,
            operation="issue",
        )
        if not exhausted:
            issued = _create(
                coordinator=coordinator,
                user=user,
                now=now,
                key_version=key_version,
            )
            audit(
                membership=membership,
                user=user,
                event_type=EventType.IOT_COORDINATOR_CREDENTIAL_ISSUED,
                entity_type=ENTITY_TYPE,
                entity_id=issued.credential.pk,
                after=_snapshot(issued.credential),
            )
    if exhausted:
        raise CoordinatorGenerationExhausted()
    if issued is None:  # pragma: no cover - invariante
        raise RuntimeError("no se emitió la credencial")
    return issued


def rotate_coordinator_credential(
    *,
    user: User,
    farm_id: int,
    coordinator_uid,
    cause=CoordinatorRevocationCause.ROTATED,
    reason=None,
) -> IssuedCoordinatorCredential:
    """Revoca la credencial vigente y emite otra en la misma transacción.

    ``cause``: ``rotated`` (rotación ordinaria) o ``reissued`` (clave perdida).
    Sin ventana de coexistencia.
    """
    issued = None
    exhausted = False
    with transaction.atomic():
        membership = lock_manage_membership(user=user, farm_id=farm_id)
        if cause not in ROTATION_CAUSES:
            raise AppError(INVALID_CAUSE, 400)
        clean = clean_reason(reason)
        coordinator = _lock_active_coordinator(farm_id=farm_id, coordinator_uid=coordinator_uid)
        current = _lock_active_credential(coordinator)
        if current is None:
            raise AppError(CREDENTIAL_NOT_FOUND, 404)
        generation = _lock_generation(coordinator)
        _require_generation_coherent(current=current, generation=generation)
        _ensure_counter_not_below_history(coordinator=coordinator, generation=generation)
        now = timezone.now()
        before = _snapshot(current)
        if generation.last_key_version >= COORDINATOR_KEY_VERSION_MAX:
            _audit_generation_exhausted(
                membership=membership,
                user=user,
                coordinator=coordinator,
                entity_id=current.pk,
                operation="rotate",
            )
            exhausted = True
        else:
            exhausted = False
            next_version = generation.last_key_version + 1
            _revoke(current, user=user, now=now, cause=cause)
            issued = _create(
                coordinator=coordinator,
                user=user,
                now=now,
                key_version=next_version,
            )
            _advance_generation(generation, next_version=next_version, now=now)
            metadata = {"cause": str(cause), "previous_coordcred_id": current.pk}
            if cause == CoordinatorRevocationCause.REISSUED:
                # Un dispositivo reemplazado o reflasheado vuelve a boot_counter 0;
                # la clave anterior ya está revocada y no puede autenticar.
                metadata["boot_window_rows_reset"] = _reset_boot_window(coordinator)
            audit(
                membership=membership,
                user=user,
                event_type=EventType.IOT_COORDINATOR_CREDENTIAL_ROTATED,
                entity_type=ENTITY_TYPE,
                entity_id=issued.credential.pk,
                before=before,
                after=_snapshot(issued.credential),
                reason=clean,
                metadata=metadata,
            )
    if exhausted:
        raise CoordinatorGenerationExhausted()
    if issued is None:  # pragma: no cover - invariante
        raise RuntimeError("no se emitió la credencial")
    return issued


def revoke_coordinator_credential(
    *,
    user: User,
    farm_id: int,
    coordinator_uid,
    cause=CoordinatorRevocationCause.ADMIN,
    reason=None,
) -> CoordinatorCredential:
    """Revoca la credencial vigente (``admin`` o ``compromised``)."""
    with transaction.atomic():
        membership = lock_manage_membership(user=user, farm_id=farm_id)
        if cause not in REVOCATION_EVENTS:
            raise AppError(INVALID_CAUSE, 400)
        clean = clean_reason(reason)
        uid = parse_uid(coordinator_uid, IdentifierKind.COORDINATOR, COORDINATOR_NOT_FOUND)
        coordinator = lock_by_uid(
            Coordinator, farm_id=farm_id, uid=uid, not_found=COORDINATOR_NOT_FOUND
        )
        current = _lock_active_credential(coordinator)
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


def revoke_for_deactivated_coordinator(*, membership, user, coordinator, now, reason="") -> int:
    """Revoca las credenciales vigentes de un coordinador ya bloqueado.

    La invoca ``devices.deactivate_coordinator`` dentro de su transacción. En la
    práctica hay una sola vigente (``UNIQUE (coordinator, active_slot)``), pero
    se recorren todas por contrato.
    """
    active = list(
        CoordinatorCredential.objects.select_for_update().filter(
            coordinator=coordinator, active_slot=ACTIVE_SLOT
        )
    )
    for credential in active:
        before = _snapshot(credential)
        _revoke(
            credential,
            user=user,
            now=now,
            cause=CoordinatorRevocationCause.COORDINATOR_DEACTIVATED,
        )
        audit(
            membership=membership,
            user=user,
            event_type=EventType.IOT_COORDINATOR_CREDENTIAL_CASCADE_REVOKED,
            entity_type=ENTITY_TYPE,
            entity_id=credential.pk,
            before=before,
            after=_snapshot(credential),
            reason=reason,
            metadata={"cause": str(CoordinatorRevocationCause.COORDINATOR_DEACTIVATED)},
        )
    return len(active)


# --- Lectura ---


def list_coordinator_credentials(*, user: User, farm_id: int, coordinator_uid):
    """Historial de credenciales de un coordinador de la finca (sin claves)."""
    require_read_membership(user=user, farm_id=farm_id)
    uid = parse_uid(coordinator_uid, IdentifierKind.COORDINATOR, COORDINATOR_NOT_FOUND)
    coordinator = Coordinator.objects.filter(farm_id=farm_id, uid=uid).first()
    if coordinator is None:
        raise AppError(COORDINATOR_NOT_FOUND, 404)
    return CoordinatorCredential.objects.filter(coordinator=coordinator)


@sensitive_variables("frame", "hmac_context")
def verify_coordinator_frame(*, frame, hmac_context) -> AuthenticatedCoordinatorFrame | None:
    """Autentica una trama ya interpretada con la credencial vigente.

    ``hmac_context`` es el contexto fijo del contrato LoRa; para la trama v1 es
    ``apps.iot.lora_frame.LORA_FRAME_HMAC_CONTEXT`` (``agrotech:lora-frame:v1``).
    Se recibe como dependencia explícita.
    Devuelve la identidad resuelta desde el coordinador registrado o ``None``.
    No distingue el motivo del rechazo y no escribe nada: la ventana anti-replay
    se actualiza después, en la transacción de la futura ingestión. Esta función
    no implementa M-03 (verificación y aceptación atómicas).
    """
    try:
        frame = crypto.validate_coordinator_frame(frame)
    except crypto.InvalidLoraTag:
        return None
    credential = (
        CoordinatorCredential.objects.select_related("coordinator__farm")
        .filter(coordinator__uid=frame.coordinator_uid, active_slot=ACTIVE_SLOT)
        .first()
    )
    if credential is None:
        _dummy_hmac(frame, hmac_context)
        return None
    key_ring = load_master_key_ring()
    if not crypto.master_key_version_supported(key_ring, credential.master_key_version):
        logger.warning(
            "credencial de coordinador %s con versión de clave maestra no soportada",
            credential.pk,
        )
        _dummy_hmac(frame, hmac_context)
        return None
    try:
        key = crypto.decrypt_coordinator_key(
            credential.ciphertext,
            credential.nonce,
            coordinator_uid=frame.coordinator_uid,
            key_ring=key_ring,
            master_key_version=credential.master_key_version,
        )
    except crypto.CoordinatorCredentialUnreadable:
        logger.warning("credencial de coordinador %s ilegible", credential.pk)
        _dummy_hmac(frame, hmac_context)
        return None
    if not crypto.verify_lora_hmac(
        key, frame.signed_bytes, frame.tag, context=hmac_context
    ):
        return None
    # La generación solo se compara después de verificar el HMAC: una trama no
    # autenticada no debe revelar qué generación tiene la credencial vigente.
    if credential.key_version is None:
        return None
    if frame.key_version != credential.key_version:
        return None
    coordinator = credential.coordinator
    if not (coordinator.is_active and coordinator.farm.is_active):
        return None
    return AuthenticatedCoordinatorFrame(
        coordinator_id=coordinator.pk,
        coordinator_uid=coordinator.uid,
        farm_id=coordinator.farm_id,
        credential_id=credential.pk,
        boot_counter=frame.boot_counter,
        seq=frame.seq,
        key_version=credential.key_version,
    )


def accept_coordinator_sequence(*, frame, now=None) -> None:
    """Acepta el ``boot_counter`` de una trama autenticada en la ventana.

    Ventana de ``BOOT_WINDOW_SIZE`` (cuatro) arranques: el mayor visto y los tres
    anteriores. Un ``boot_counter`` mayor abre ventana; uno dentro de la ventana
    se acepta aunque llegue tarde; uno anterior a la ventana se rechaza con
    ``SequenceOutOfWindow``. Las secuencias desordenadas dentro de un mismo
    arranque se aceptan: aquí no se guarda ``seq``.

    **Dependencia de la futura ingestión:** esta función no deduplica ni usa
    ``credential_id``. La identidad única aprobada
    ``(coordinator_id, credential_id, boot_counter, seq)``, la inserción de
    mediciones y esta aceptación deben compartir una sola transacción, y un
    fallo de persistencia no puede dejar la ventana avanzada. Debe invocarse dentro de
    ``transaction.atomic()`` del llamador; si no, lanza ``RuntimeError``.
    """
    if not isinstance(frame, AuthenticatedCoordinatorFrame):
        raise TypeError("se esperaba una trama autenticada")
    if not isinstance(frame.coordinator_uid, str):
        raise TypeError("coordinator_uid no válido")
    for name in ("coordinator_id", "farm_id", "credential_id", "key_version"):
        value = getattr(frame, name)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise TypeError(f"{name} no válido")
    if not isinstance(frame.boot_counter, int) or isinstance(frame.boot_counter, bool):
        raise TypeError("boot_counter no válido")
    if frame.boot_counter < 0:
        raise ValueError("boot_counter no válido")
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError(NOT_IN_TRANSACTION)

    # Bloqueo y autorización por el patrón existente: finca + uid persistidos.
    coordinator = lock_by_uid(
        Coordinator,
        farm_id=frame.farm_id,
        uid=frame.coordinator_uid,
        not_found=COORDINATOR_NOT_FOUND,
    )
    if (
        coordinator.pk != frame.coordinator_id
        or coordinator.uid != frame.coordinator_uid
        or coordinator.farm_id != frame.farm_id
    ):
        raise AppError(COORDINATOR_NOT_FOUND, 404)
    if not coordinator.is_active or not coordinator.farm.is_active:
        raise AppError(COORDINATOR_INACTIVE, 409)

    moment = now or timezone.now()
    rows = list(
        CoordinatorBootWindow.objects.select_for_update()
        .filter(coordinator=coordinator)
        .order_by("-boot_counter")
    )
    highest = rows[0].boot_counter if rows else None
    if highest is not None and frame.boot_counter < highest - (BOOT_WINDOW_SIZE - 1):
        raise SequenceOutOfWindow()

    for row in rows:
        if row.boot_counter == frame.boot_counter:
            row.last_seen_at = moment
            row.save(update_fields=["last_seen_at"])
            return

    CoordinatorBootWindow.objects.create(
        coordinator=coordinator,
        boot_counter=frame.boot_counter,
        first_seen_at=moment,
        last_seen_at=moment,
    )
    new_highest = frame.boot_counter if highest is None else max(highest, frame.boot_counter)
    oldest_kept = new_highest - (BOOT_WINDOW_SIZE - 1)
    CoordinatorBootWindow.objects.filter(
        coordinator=coordinator, boot_counter__lt=oldest_kept
    ).delete()


# --- Internos ---


def _lock_active_coordinator(*, farm_id: int, coordinator_uid) -> Coordinator:
    uid = parse_uid(coordinator_uid, IdentifierKind.COORDINATOR, COORDINATOR_NOT_FOUND)
    coordinator = lock_by_uid(
        Coordinator, farm_id=farm_id, uid=uid, not_found=COORDINATOR_NOT_FOUND
    )
    if not coordinator.is_active:
        raise AppError(COORDINATOR_INACTIVE, 409)
    return coordinator


def _lock_active_credential(coordinator: Coordinator) -> CoordinatorCredential | None:
    return (
        CoordinatorCredential.objects.select_for_update()
        .filter(coordinator=coordinator, active_slot=ACTIVE_SLOT)
        .first()
    )


def _lock_generation(coordinator: Coordinator) -> CoordinatorKeyGeneration | None:
    return (
        CoordinatorKeyGeneration.objects.select_for_update()
        .filter(coordinator=coordinator)
        .first()
    )


def _allocate_generation(
    *,
    coordinator: Coordinator,
    generation: CoordinatorKeyGeneration | None,
    now,
    membership,
    user,
    entity_id: int,
    operation: str,
) -> tuple[int | None, CoordinatorKeyGeneration, bool]:
    """Reserva la siguiente generación o detecta legado/agotamiento.

    La primera emisión sin historial crea el contador en 1; cualquier coordinador
    con credenciales y sin contador es legacy y falla cerrado, sin backfill.
    """
    if generation is None:
        if CoordinatorCredential.objects.filter(coordinator=coordinator).exists():
            raise LegacyGenerationUnresolved()
        generation = CoordinatorKeyGeneration.objects.create(
            coordinator=coordinator,
            last_key_version=COORDINATOR_KEY_VERSION_MIN,
            updated_at=now,
        )
        return COORDINATOR_KEY_VERSION_MIN, generation, False
    _ensure_counter_not_below_history(coordinator=coordinator, generation=generation)
    if generation.last_key_version >= COORDINATOR_KEY_VERSION_MAX:
        _audit_generation_exhausted(
            membership=membership,
            user=user,
            coordinator=coordinator,
            entity_id=entity_id,
            operation=operation,
        )
        return None, generation, True
    next_version = generation.last_key_version + 1
    _advance_generation(generation, next_version=next_version, now=now)
    return next_version, generation, False


def _require_generation_coherent(
    *, current: CoordinatorCredential, generation: CoordinatorKeyGeneration | None
) -> None:
    if generation is None or current.key_version is None:
        raise LegacyGenerationUnresolved()
    if current.key_version != generation.last_key_version:
        raise CoordinatorGenerationInconsistent()


def _ensure_counter_not_below_history(
    *, coordinator: Coordinator, generation: CoordinatorKeyGeneration
) -> None:
    if CoordinatorCredential.objects.filter(
        coordinator=coordinator, key_version__gt=generation.last_key_version
    ).exists():
        raise CoordinatorGenerationInconsistent()


def _advance_generation(
    generation: CoordinatorKeyGeneration, *, next_version: int, now
) -> None:
    generation.last_key_version = next_version
    generation.updated_at = now
    generation.save(update_fields=["last_key_version", "updated_at"])


def _audit_generation_exhausted(
    *, membership, user, coordinator: Coordinator, entity_id: int, operation: str
) -> None:
    audit(
        membership=membership,
        user=user,
        event_type=EventType.IOT_COORDINATOR_CREDENTIAL_GENERATION_EXHAUSTED,
        entity_type=ENTITY_TYPE,
        entity_id=entity_id,
        after={
            "coordinator_uid": coordinator.uid,
            "last_key_version": COORDINATOR_KEY_VERSION_MAX,
        },
        metadata={
            "operation": operation,
            "last_key_version": COORDINATOR_KEY_VERSION_MAX,
        },
    )


@sensitive_variables("key", "encrypted")
def _create(
    *, coordinator: Coordinator, user, now, key_version: int
) -> IssuedCoordinatorCredential:
    key = crypto.generate_coordinator_key()
    key_ring = load_master_key_ring()
    version = key_ring.current_version
    if not isinstance(version, int) or isinstance(version, bool):
        raise crypto.InvalidEncryptedCredential()
    if not 1 <= version <= crypto.MASTER_KEY_VERSION_MAX:
        raise crypto.InvalidEncryptedCredential()
    encrypted = crypto.encrypt_coordinator_key(
        key,
        coordinator_uid=coordinator.uid,
        key_ring=key_ring,
        master_key_version=version,
    )
    try:
        with transaction.atomic():
            credential = CoordinatorCredential.objects.create(
                coordinator=coordinator,
                ciphertext=encrypted.ciphertext,
                nonce=encrypted.nonce,
                master_key_version=encrypted.master_key_version,
                key_version=key_version,
                active_slot=ACTIVE_SLOT,
                issued_at=now,
                issued_by=user,
            )
    except IntegrityError:
        # El punto de guardado ya se revirtió. Solo el conflicto real de
        # credencial activa se traduce a 409; cualquier otra restricción se
        # propaga sin ocultar su causa.
        if CoordinatorCredential.objects.filter(
            coordinator=coordinator, active_slot=ACTIVE_SLOT
        ).exists():
            raise AppError(ALREADY_HAS_CREDENTIAL, 409) from None
        raise
    return IssuedCoordinatorCredential(credential=credential, key=key)


def _reset_boot_window(coordinator: Coordinator) -> int:
    """Borra la ventana anti-replay del coordinador (solo en reemisión)."""
    rows = list(
        CoordinatorBootWindow.objects.select_for_update().filter(coordinator=coordinator)
    )
    if rows:
        CoordinatorBootWindow.objects.filter(
            pk__in=[row.pk for row in rows]
        ).delete()
    return len(rows)


def _revoke(credential: CoordinatorCredential, *, user, now, cause) -> None:
    credential.active_slot = None
    credential.revoked_at = now
    credential.revoked_by = user
    credential.revocation_cause = str(cause)
    credential.save(
        update_fields=["active_slot", "revoked_at", "revoked_by", "revocation_cause"]
    )


def _snapshot(credential: CoordinatorCredential) -> dict:
    """Datos auditables: nunca la clave, el ciphertext ni el nonce.

    Las claves no contienen ``credential``: ``record_audit_event`` rechaza todo
    nombre que contenga un término sensible, y esa protección no se relaja.
    """
    return {
        "coordcred_id": credential.pk,
        "coordinator_uid": credential.coordinator.uid,
        "key_version": credential.key_version,
        "master_key_version": credential.master_key_version,
        "active": credential.is_active,
        "revocation_cause": credential.revocation_cause,
    }


@sensitive_variables("frame", "hmac_context")
def _dummy_hmac(frame, hmac_context) -> None:
    """Calcula un HMAC descartable para no revelar el motivo por el tiempo."""
    crypto.compute_lora_hmac(_DUMMY_KEY, frame.signed_bytes, context=hmac_context)