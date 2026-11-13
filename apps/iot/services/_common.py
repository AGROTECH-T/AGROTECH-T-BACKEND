"""Utilidades internas de los servicios IoT (IoT-02.2).

Propósito: validación de textos, búsqueda por identificador público, creación
con reintento ante colisión de ``uid`` y registro de auditoría.
"""
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.accounts.errors import AppError
from apps.iot.identifiers import (
    MAX_GENERATION_ATTEMPTS,
    IdentifierKind,
    InvalidIdentifierError,
    generate_identifier,
    normalize_identifier,
)
from apps.trazabilidad.services import REASON_TOO_LONG, normalize_reason, record_audit_event

UID_GENERATION_FAILED = "no se pudo generar un identificador de dispositivo"
REASON_REQUIRED = "el motivo es obligatorio"


def required_text(value, empty_message: str, max_length: int) -> str:
    """Normaliza un texto obligatorio (mismo criterio que inventario)."""
    if not isinstance(value, str):
        raise AppError(empty_message, 400)
    cleaned = value.strip()
    if not cleaned:
        raise AppError(empty_message, 400)
    if len(cleaned) > max_length:
        raise AppError(f"no puede superar los {max_length} caracteres", 400)
    return cleaned


def optional_text(value, max_length: int) -> str:
    """Normaliza un texto opcional; ``None`` equivale a vacío."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise AppError("texto no válido", 400)
    cleaned = value.strip()
    if len(cleaned) > max_length:
        raise AppError(f"no puede superar los {max_length} caracteres", 400)
    return cleaned


def required_reason(reason) -> str:
    """Motivo obligatorio para procedimientos administrativos (reactivación)."""
    clean = clean_reason(reason)
    if not clean:
        raise AppError(REASON_REQUIRED, 400)
    return clean


def clean_reason(reason) -> str:
    """Motivo opcional con el límite de ``AuditEvent.reason``."""
    if reason is not None and not isinstance(reason, str):
        raise AppError("motivo no válido", 400)
    try:
        return normalize_reason(reason)
    except ValidationError:
        raise AppError(REASON_TOO_LONG, 400) from None


def parse_uid(value, kind: IdentifierKind, not_found: str) -> str:
    """Normaliza un ``uid``; un formato inválido equivale a "no encontrado"."""
    try:
        return normalize_identifier(value, kind)
    except InvalidIdentifierError:
        raise AppError(not_found, 404) from None


def lock_by_uid(model, *, farm_id: int, uid: str, not_found: str):
    """Bloquea un dispositivo de la finca por ``uid`` o responde 404.

    Filtrar por ``farm_id`` evita revelar dispositivos de otra finca.
    """
    instance = model.objects.select_for_update().filter(farm_id=farm_id, uid=uid).first()
    if instance is None:
        raise AppError(not_found, 404)
    return instance


def create_with_uid(model, kind: IdentifierKind, **fields):
    """Crea un dispositivo con ``uid`` nuevo, reintentando ante colisión.

    La unicidad la garantiza ``UNIQUE (uid)`` en MariaDB. Cada intento usa un
    punto de guardado, así una colisión no aborta la transacción externa. Otros
    errores de integridad se propagan al llamador.
    """
    for _ in range(MAX_GENERATION_ATTEMPTS):
        uid = generate_identifier(kind)
        try:
            with transaction.atomic():
                return model.objects.create(uid=uid, **fields)
        except IntegrityError:
            if not model.objects.filter(uid=uid).exists():
                raise
    raise AppError(UID_GENERATION_FAILED, 500)


def audit(*, membership, user, event_type, entity_type, entity_id, before=None,
          after=None, reason="", metadata=None) -> None:
    """Registra el evento en la transacción del llamador (sin secretos)."""
    record_audit_event(
        farm=membership.farm,
        actor=user,
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        before=before,
        after=after,
        reason=reason,
        metadata=metadata,
    )
