"""Autorizaciones coordinador–gateway (IoT-02.2, DEC-13).

Propósito: autorizar a un coordinador para enviar sus tramas por un gateway de
su misma finca, revocar esa autorización y consultar su historial.

Contexto: cada autorización es una fila de ``CoordinatorGateway``. Revocar no
borra: cierra el periodo y libera ``active_pair``. Reautorizar crea una fila
nueva. La misma finca la exigen el filtro por ``farm_id`` y las FK compuestas.
"""
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.errors import AppError
from apps.accounts.models import User
from apps.iot.identifiers import IdentifierKind
from apps.iot.models import ACTIVE_PAIR, Coordinator, CoordinatorGateway, Gateway
from apps.iot.services._common import audit, clean_reason, lock_by_uid, parse_uid
from apps.iot.services.access import lock_manage_membership, require_read_membership
from apps.trazabilidad.models import EventType

ENTITY_TYPE = "iot_coordinator_gateway"
COORDINATOR_NOT_FOUND = "coordinador no encontrado"
GATEWAY_NOT_FOUND = "gateway no encontrado"
AUTHORIZATION_NOT_FOUND = "autorización no encontrada"
ALREADY_AUTHORIZED = "el coordinador ya está autorizado en ese gateway"
INACTIVE_DEVICE = "el coordinador y el gateway deben estar activos"


def authorize_gateway(
    *, user: User, farm_id: int, coordinator_uid: str, gateway_uid: str
) -> CoordinatorGateway:
    """Autoriza al coordinador en el gateway (ambos activos y de la finca)."""
    with transaction.atomic():
        membership = lock_manage_membership(user=user, farm_id=farm_id)
        coordinator_uid = parse_uid(
            coordinator_uid, IdentifierKind.COORDINATOR, COORDINATOR_NOT_FOUND
        )
        gateway_uid = parse_uid(gateway_uid, IdentifierKind.GATEWAY, GATEWAY_NOT_FOUND)
        coordinator = lock_by_uid(
            Coordinator, farm_id=farm_id, uid=coordinator_uid, not_found=COORDINATOR_NOT_FOUND
        )
        gateway = lock_by_uid(
            Gateway, farm_id=farm_id, uid=gateway_uid, not_found=GATEWAY_NOT_FOUND
        )
        if not (coordinator.is_active and gateway.is_active):
            raise AppError(INACTIVE_DEVICE, 409)
        try:
            with transaction.atomic():
                authorization = CoordinatorGateway.objects.create(
                    farm_id=membership.farm_id,
                    coordinator=coordinator,
                    gateway=gateway,
                    authorized_at=timezone.now(),
                    authorized_by=user,
                    active_pair=ACTIVE_PAIR,
                )
        except IntegrityError:
            raise AppError(ALREADY_AUTHORIZED, 409) from None
        audit(
            membership=membership,
            user=user,
            event_type=EventType.IOT_GATEWAY_AUTHORIZED,
            entity_type=ENTITY_TYPE,
            entity_id=authorization.pk,
            after=_snapshot(authorization),
        )
        return authorization


def revoke_gateway_authorization(
    *, user: User, farm_id: int, coordinator_uid: str, gateway_uid: str, reason=None
) -> CoordinatorGateway:
    """Revoca la autorización vigente del coordinador en el gateway."""
    with transaction.atomic():
        membership = lock_manage_membership(user=user, farm_id=farm_id)
        coordinator_uid = parse_uid(
            coordinator_uid, IdentifierKind.COORDINATOR, COORDINATOR_NOT_FOUND
        )
        gateway_uid = parse_uid(gateway_uid, IdentifierKind.GATEWAY, GATEWAY_NOT_FOUND)
        clean = clean_reason(reason)
        coordinator = lock_by_uid(
            Coordinator, farm_id=farm_id, uid=coordinator_uid, not_found=COORDINATOR_NOT_FOUND
        )
        gateway = lock_by_uid(
            Gateway, farm_id=farm_id, uid=gateway_uid, not_found=GATEWAY_NOT_FOUND
        )
        authorization = (
            CoordinatorGateway.objects.select_for_update()
            .filter(coordinator=coordinator, gateway=gateway, active_pair=ACTIVE_PAIR)
            .first()
        )
        if authorization is None:
            raise AppError(AUTHORIZATION_NOT_FOUND, 404)
        revoke_locked(
            membership=membership,
            user=user,
            authorization=authorization,
            now=timezone.now(),
            reason=clean,
        )
        return authorization


def revoke_locked(*, membership, user, authorization, now, reason="", cause=None) -> None:
    """Revoca una autorización ya bloqueada y la audita (uso interno)."""
    before = _snapshot(authorization)
    authorization.revoked_at = now
    authorization.revoked_by = user
    authorization.active_pair = None
    authorization.save(update_fields=["revoked_at", "revoked_by", "active_pair"])
    audit(
        membership=membership,
        user=user,
        event_type=EventType.IOT_GATEWAY_AUTHORIZATION_REVOKED,
        entity_type=ENTITY_TYPE,
        entity_id=authorization.pk,
        before=before,
        after=_snapshot(authorization),
        reason=reason,
        metadata={"cause": cause} if cause else None,
    )


def lock_active_authorizations(**filters):
    """Bloquea autorizaciones vigentes en orden de PK (uso interno)."""
    return list(
        CoordinatorGateway.objects.select_for_update()
        .filter(active_pair=ACTIVE_PAIR, **filters)
        .select_related("coordinator", "gateway")
        .order_by("pk")
    )


def list_coordinator_authorizations(
    *, user: User, farm_id: int, coordinator_uid: str, include_revoked: bool = True
):
    """Historial de autorizaciones de un coordinador de la finca."""
    require_read_membership(user=user, farm_id=farm_id)
    coordinator_uid = parse_uid(coordinator_uid, IdentifierKind.COORDINATOR, COORDINATOR_NOT_FOUND)
    coordinator = Coordinator.objects.filter(farm_id=farm_id, uid=coordinator_uid).first()
    if coordinator is None:
        raise AppError(COORDINATOR_NOT_FOUND, 404)
    queryset = CoordinatorGateway.objects.filter(
        farm_id=farm_id, coordinator=coordinator
    ).select_related("gateway")
    if not include_revoked:
        queryset = queryset.filter(active_pair=ACTIVE_PAIR)
    return queryset


def _snapshot(authorization: CoordinatorGateway) -> dict:
    return {
        "coordinator_uid": authorization.coordinator.uid,
        "gateway_uid": authorization.gateway.uid,
        "active": authorization.is_active,
    }
