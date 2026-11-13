"""Concesión y retiro de la capacidad global de crear fincas.

Propósito: cambiar ``User.can_create_farms`` solo por una vía explícita,
idempotente y auditada (DEC-11).
Contexto: se invoca desde los comandos de gestión ``grant_farm_creation`` y
``revoke_farm_creation``; no existe endpoint HTTP para concederla. Aceptar una
invitación o abandonar una finca nunca modifica esta capacidad. La única
concesión automática es la de la primera finca (F10.2, en
``apps.farms.services.create_farm``), que se registra con el mismo evento y
``metadata.execution_source = "first_farm_onboarding"``.

Auditoría: el motivo es obligatorio. ``actor`` queda en None porque no hay una
cuenta autenticada de AGROTECH que represente al operador; en su lugar,
``metadata.executed_by`` guarda el usuario del sistema operativo que ejecutó el
comando. Es una referencia de ejecución local, no una identidad autenticada de
la plataforma, y puede no estar disponible.
"""
import getpass

from django.db import transaction

from apps.accounts.errors import AppError
from apps.accounts.models import User
from apps.trazabilidad.models import EventType
from apps.trazabilidad.services import (
    AUDIT_REASON_MAX_LENGTH,
    REASON_TOO_LONG,
    record_audit_event,
)

ACCOUNT_NOT_FOUND = "cuenta no encontrada"
REASON_REQUIRED = "el motivo es obligatorio"
UNKNOWN_EXECUTOR = "desconocido"
EXECUTION_SOURCE = "management_command"


def local_executor() -> str:
    """Usuario del sistema operativo que ejecuta el proceso (sin secretos)."""
    try:
        name = getpass.getuser()
    except (OSError, KeyError, ImportError):
        return UNKNOWN_EXECUTOR
    return (name or UNKNOWN_EXECUTOR).strip()[:80] or UNKNOWN_EXECUTOR


def set_farm_creation(
    *,
    identification: str,
    allowed: bool,
    reason: str,
    executed_by: str | None = None,
) -> bool:
    """Concede o retira la capacidad; devuelve True si cambió el valor.

    Exige un motivo no vacío. Si el valor ya era el pedido no escribe ni
    audita (idempotente). El cambio y su evento se confirman juntos: si la
    auditoría falla, el campo no cambia.
    """
    clean_reason = (reason or "").strip()
    if not clean_reason:
        raise AppError(REASON_REQUIRED, 400)
    if len(clean_reason) > AUDIT_REASON_MAX_LENGTH:
        raise AppError(REASON_TOO_LONG, 400)
    executor = (executed_by or "").strip() or local_executor()
    with transaction.atomic():
        user = User.objects.select_for_update().filter(identification=identification).first()
        if user is None:
            raise AppError(ACCOUNT_NOT_FOUND, 404)
        if user.can_create_farms == allowed:
            return False
        previous = user.can_create_farms
        user.can_create_farms = allowed
        user.save(update_fields=["can_create_farms"])
        record_audit_event(
            farm=None,
            actor=None,
            event_type=(
                EventType.FARM_CREATION_GRANTED if allowed else EventType.FARM_CREATION_REVOKED
            ),
            entity_type="User",
            entity_id=str(user.pk),
            before={"can_create_farms": previous},
            after={"can_create_farms": allowed},
            reason=clean_reason,
            metadata={"executed_by": executor, "execution_source": EXECUTION_SOURCE},
        )
        return True
