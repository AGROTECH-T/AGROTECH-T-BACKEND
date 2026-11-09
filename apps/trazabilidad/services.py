"""Servicio central de auditoría.

Propósito: registrar eventos inmutables de forma segura, dentro de la misma
transacción que la operación auditada. No abre su propio ``atomic()``: si la
escritura falla, propaga la excepción para que la operación de negocio revierta.
"""
import json

from django.core.exceptions import ValidationError

from apps.trazabilidad.models import AuditEvent, EventType

# Claves consideradas sensibles; se rechazan (por nombre) en los payloads.
SENSITIVE_KEYS = frozenset(
    {
        "password",
        "passwd",
        "token",
        "secret",
        "otp",
        "cookie",
        "session",
        "credential",
        "credentials",
        "api_key",
        "apikey",
        "access_token",
        "refresh_token",
        "clave",
        "contraseña",
        "document",
        "documento",
        "identification",
        "identificacion",
        "email",
        "correo",
        "phone",
        "telefono",
        "celular",
    }
)


def _normalize_key(name) -> str:
    """Normaliza un nombre de clave: minúsculas y sin separadores."""
    return (
        str(name)
        .lower()
        .replace("-", "")
        .replace("_", "")
        .replace(" ", "")
        .replace(".", "")
    )


NORMALIZED_SENSITIVE_KEYS = frozenset(_normalize_key(key) for key in SENSITIVE_KEYS)


def record_audit_event(
    *,
    farm=None,
    actor=None,
    event_type: str,
    entity_type: str,
    entity_id,
    before: dict | None = None,
    after: dict | None = None,
    reason: str = "",
    metadata: dict | None = None,
) -> AuditEvent:
    """Crea un evento de auditoría dentro de la transacción del llamador.

    Valida el tipo de evento, la entidad y la serialización de los payloads, y
    rechaza datos sensibles. No abre transacción propia.
    """
    if event_type not in EventType.values:
        raise ValidationError(f"tipo de evento no válido: {event_type!r}")
    if not entity_type or not str(entity_id).strip():
        raise ValidationError("entity_type y entity_id son obligatorios.")

    before = _clean_payload(before, "before_data")
    after = _clean_payload(after, "after_data")
    metadata = _clean_payload(metadata, "metadata")

    return AuditEvent.objects.create(
        event_type=event_type,
        farm=farm,
        farm_identifier=farm.pk if farm is not None else None,
        actor=actor,
        actor_identifier=actor.pk if actor is not None else None,
        actor_display=_actor_display(actor),
        entity_type=entity_type,
        entity_id=str(entity_id),
        before_data=before,
        after_data=after,
        reason=(reason or "").strip()[:200],
        metadata=metadata or {},
    )


def _actor_display(actor) -> str:
    """Arma un identificador visible mínimo (nombre y apellido)."""
    if actor is None:
        return ""
    parts = (getattr(actor, "first_name", ""), getattr(actor, "last_name", ""))
    return " ".join(part for part in parts if part).strip()


def _clean_payload(payload: dict | None, label: str) -> dict | None:
    """Valida que el payload sea un dict JSON serializable y sin claves sensibles."""
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise ValidationError(f"{label} debe ser un objeto JSON (dict).")
    _reject_sensitive_keys(payload, label)
    try:
        json.dumps(payload)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{label} no es serializable a JSON.") from exc
    return payload


def _reject_sensitive_keys(node, label: str, active=None) -> None:
    """Rechaza claves sensibles en diccionarios, listas y tuplas anidados.

    Recorre todas las estructuras anidadas y detecta ciclos mediante el conjunto
    de contenedores en el camino activo, rechazándolos de forma controlada.
    """
    if not isinstance(node, (dict, list, tuple)):
        return
    if active is None:
        active = set()
    node_id = id(node)
    if node_id in active:
        raise ValidationError(f"{label} contiene una estructura cíclica no permitida.")
    active.add(node_id)
    try:
        if isinstance(node, dict):
            for key, value in node.items():
                _check_sensitive_key(key, label)
                _reject_sensitive_keys(value, label, active)
        else:
            for item in node:
                _reject_sensitive_keys(item, label, active)
    finally:
        active.discard(node_id)


def _check_sensitive_key(key, label: str) -> None:
    """Rechaza una clave si su forma normalizada contiene un nombre sensible."""
    normalized = _normalize_key(key)
    for sensitive in NORMALIZED_SENSITIVE_KEYS:
        if sensitive in normalized:
            raise ValidationError(f"{label} contiene una clave sensible: {key!r}")
