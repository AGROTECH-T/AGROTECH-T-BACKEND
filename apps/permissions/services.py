"""Servicios centrales de autorización de permisos.

Propósito: calcular permisos efectivos, comprobar permisos específicos y
administrar asignaciones y restricciones con auditoría transaccional.

Contexto: implementa la fórmula ``(P_rol ∩ P_asignados) − P_restringidos`` para
una membresía concreta, validando que la finca, la membresía y el usuario estén
activos. Las escrituras exigen un administrador activo de la misma finca,
bloquean ``Farm`` y las membresías por PK, y auditan dentro de la misma
transacción.
"""
from django.db import transaction

from apps.accounts.errors import AppError
from apps.farms.models import Farm, FarmMembership, FarmRole, MembershipStatus
from apps.permissions.catalog import (
    CATEGORY_OPERATIONAL,
    PERMISSION_CATALOG,
    role_ceiling,
)
from apps.permissions.models import (
    AssignmentSource,
    PermissionAssignment,
    PermissionRestriction,
)
from apps.trazabilidad.models import EventType
from apps.trazabilidad.services import record_audit_event


def assigned(membership) -> frozenset:
    """Permisos asignados explícitamente a una membresía (sin techo ni restricciones)."""
    rows = PermissionAssignment.objects.filter(membership=membership).values_list(
        "module", "action"
    )
    return frozenset(f"{module}.{action}" for module, action in rows)


def restricted(membership) -> frozenset:
    """Restricciones activas de una membresía."""
    rows = PermissionRestriction.objects.filter(membership=membership).values_list(
        "module", "action"
    )
    return frozenset(f"{module}.{action}" for module, action in rows)


def _is_active(membership) -> bool:
    """Valida que finca, membresía y usuario estén activos."""
    return (
        membership.status == MembershipStatus.ACTIVE
        and membership.farm.is_active
        and membership.user.is_active
    )


def effective(membership) -> frozenset:
    """Calcula los permisos efectivos: (techo ∩ asignados) − restringidos.

    Si la finca, la membresía o el usuario no están activos, devuelve un
    conjunto vacío (denegación). No devuelve permisos por defecto.
    """
    if not _is_active(membership):
        return frozenset()
    ceiling = role_ceiling(membership.role)
    return (ceiling & assigned(membership)) - restricted(membership)


def has(membership, module: str, action: str) -> bool:
    """Comprueba si un permiso es efectivo para la membresía."""
    return f"{module}.{action}" in effective(membership)


def _catalog_permission(module, action):
    """Retorna el permiso operativo asignable del catálogo o falla con 400."""
    key = f"{module}.{action}"
    for permission in PERMISSION_CATALOG:
        if permission.key == key:
            if (
                not permission.assignable
                or permission.category != CATEGORY_OPERATIONAL
            ):
                raise AppError("permiso no asignable", 400)
            return permission
    raise AppError("permiso no encontrado en el catálogo", 400)


def _lock_admin_and_target(actor, membership):
    """Bloquea finca y membresías y revalida estados y rol del actor.

    El orden de bloqueo es ``Farm`` y después las membresías ordenadas por PK.
    """
    if actor is None or getattr(actor, "pk", None) is None:
        raise AppError("solo un administrador activo puede administrar permisos", 403)
    if membership is None or getattr(membership, "pk", None) is None:
        raise AppError("integrante no encontrado", 404)

    farm_id = membership.farm_id
    actor_pk = (
        FarmMembership.objects.filter(user=actor, farm_id=farm_id)
        .values_list("pk", flat=True)
        .first()
    )
    if actor_pk is None:
        raise AppError("finca no encontrada", 404)

    try:
        farm = Farm.objects.select_for_update().get(pk=farm_id)
    except Farm.DoesNotExist:
        raise AppError("finca no encontrada", 404) from None

    membership_ids = sorted({int(actor_pk), int(membership.pk)})
    rows = {
        row.pk: row
        for row in FarmMembership.objects.select_for_update()
        .filter(farm_id=farm_id, pk__in=membership_ids)
        .order_by("pk")
    }
    actor_membership = rows.get(actor_pk)
    target_membership = rows.get(membership.pk)
    if (
        actor_membership is None
        or actor_membership.status != MembershipStatus.ACTIVE
        or not actor_membership.user.is_active
        or actor_membership.role != FarmRole.ADMINISTRADOR
    ):
        raise AppError("solo un administrador activo puede administrar permisos", 403)
    if not farm.is_active:
        raise AppError("finca no encontrada", 404)
    if (
        target_membership is None
        or target_membership.status != MembershipStatus.ACTIVE
        or not target_membership.user.is_active
    ):
        raise AppError("integrante no encontrado", 404)

    actor_membership.farm = farm
    target_membership.farm = farm
    return farm, actor_membership, target_membership


def _normalize_reason(reason) -> str:
    """Normaliza el motivo antes de registrarlo en auditoría."""
    if reason is None:
        return ""
    return str(reason).strip()


def _require_ceiling(target_membership, permission) -> None:
    """Rechaza permisos que superen el techo del rol objetivo."""
    if permission.key not in role_ceiling(target_membership.role):
        raise AppError("el rol del integrante no admite ese permiso", 400)


def _audit_permission(
    *,
    farm,
    actor,
    event_type,
    membership,
    module,
    action,
    before=None,
    after=None,
    reason="",
) -> None:
    """Registra el evento dentro de la transacción del llamador."""
    record_audit_event(
        farm=farm,
        actor=actor,
        event_type=event_type,
        entity_type="FarmMembership",
        entity_id=str(membership.pk),
        before=before,
        after=after,
        reason=reason,
        metadata={
            "membership_id": membership.pk,
            "module": module,
            "action": action,
        },
    )


def grant(actor, membership, module, action, reason=None) -> PermissionAssignment:
    """Asigna un permiso operativo dentro del techo del rol objetivo."""
    permission = _catalog_permission(module, action)
    with transaction.atomic():
        farm, actor_membership, target = _lock_admin_and_target(actor, membership)
        if target.pk == actor_membership.pk:
            raise AppError("no puedes asignarte permisos a ti mismo", 403)
        _require_ceiling(target, permission)
        assignment, created = PermissionAssignment.objects.get_or_create(
            membership=target,
            module=module,
            action=action,
            defaults={
                "source": AssignmentSource.ADMIN,
                "granted_by": actor,
            },
        )
        if created:
            _audit_permission(
                farm=farm,
                actor=actor,
                event_type=EventType.PERMISSION_GRANTED,
                membership=target,
                module=module,
                action=action,
                after={
                    "module": module,
                    "action": action,
                    "source": AssignmentSource.ADMIN,
                },
                reason=_normalize_reason(reason),
            )
        return assignment


def restrict(
    actor, membership, module, action, reason=None
) -> PermissionRestriction:
    """Restringe un permiso operativo dentro del techo del rol objetivo."""
    permission = _catalog_permission(module, action)
    with transaction.atomic():
        farm, _actor_membership, target = _lock_admin_and_target(actor, membership)
        _require_ceiling(target, permission)
        restriction, created = PermissionRestriction.objects.get_or_create(
            membership=target,
            module=module,
            action=action,
            defaults={"restricted_by": actor},
        )
        if created:
            _audit_permission(
                farm=farm,
                actor=actor,
                event_type=EventType.PERMISSION_RESTRICTED,
                membership=target,
                module=module,
                action=action,
                after={"module": module, "action": action},
                reason=_normalize_reason(reason),
            )
        return restriction


def ungrant(actor, membership, module, action, reason=None) -> None:
    """Retira una asignación si existe, sin duplicar auditoría."""
    _catalog_permission(module, action)
    with transaction.atomic():
        farm, _actor_membership, target = _lock_admin_and_target(actor, membership)
        assignment = PermissionAssignment.objects.filter(
            membership=target,
            module=module,
            action=action,
        ).first()
        if assignment is None:
            return None
        before = {
            "module": module,
            "action": action,
            "source": assignment.source,
        }
        assignment.delete()
        _audit_permission(
            farm=farm,
            actor=actor,
            event_type=EventType.PERMISSION_REVOKED,
            membership=target,
            module=module,
            action=action,
            before=before,
            reason=_normalize_reason(reason),
        )
    return None


def unrestrict(actor, membership, module, action, reason=None) -> None:
    """Retira una restricción si existe; exige motivo para otro administrador."""
    _catalog_permission(module, action)
    normalized_reason = _normalize_reason(reason)
    with transaction.atomic():
        farm, actor_membership, target = _lock_admin_and_target(actor, membership)
        if target.pk == actor_membership.pk:
            raise AppError("no puedes retirar tus propias restricciones", 403)
        restriction = PermissionRestriction.objects.filter(
            membership=target,
            module=module,
            action=action,
        ).first()
        if restriction is None:
            return None
        if target.role == FarmRole.ADMINISTRADOR and not normalized_reason:
            raise AppError(
                "el motivo es obligatorio para retirar restricciones a otro "
                "administrador",
                400,
            )
        before = {"module": module, "action": action}
        restriction.delete()
        _audit_permission(
            farm=farm,
            actor=actor,
            event_type=EventType.PERMISSION_UNRESTRICTED,
            membership=target,
            module=module,
            action=action,
            before=before,
            reason=normalized_reason,
        )
    return None


CLEANUP_CAUSES = frozenset({"member_left", "member_revoked", "member_reactivated"})


def clear_membership_permissions(*, membership, actor, cause) -> tuple[int, int]:
    """Elimina asignaciones y restricciones de una membresía, auditando cada fila.

    Debe invocarse dentro de la transacción del servicio llamador, que ya
    bloqueó ``Farm`` y la membresía. No abre ni confirma transacción propia ni
    adquiere bloqueos adicionales. Filtra exclusivamente por la membresía
    recibida, audita cada fila seleccionada y elimina exactamente esos
    identificadores. Un DELETE residual por membresía detecta filas no
    seleccionadas; si encuentra alguna lanza ``RuntimeError`` y depende del
    rollback del llamador. Retorna ``(asignaciones, restricciones)`` eliminadas.
    """
    if cause not in CLEANUP_CAUSES:
        raise ValueError(f"causa de limpieza no válida: {cause!r}.")
    if membership is None or getattr(membership, "pk", None) is None:
        raise ValueError("la limpieza requiere una membresía persistida.")

    farm = membership.farm
    assignments = list(
        PermissionAssignment.objects.filter(membership_id=membership.pk).order_by("pk")
    )
    restrictions = list(
        PermissionRestriction.objects.filter(membership_id=membership.pk).order_by("pk")
    )
    assignment_ids = [row.pk for row in assignments]
    restriction_ids = [row.pk for row in restrictions]

    for assignment in assignments:
        _audit_permission(
            farm=farm,
            actor=actor,
            event_type=EventType.PERMISSION_REVOKED,
            membership=membership,
            module=assignment.module,
            action=assignment.action,
            before={
                "module": assignment.module,
                "action": assignment.action,
                "source": assignment.source,
            },
            reason=cause,
        )
    for restriction in restrictions:
        _audit_permission(
            farm=farm,
            actor=actor,
            event_type=EventType.PERMISSION_UNRESTRICTED,
            membership=membership,
            module=restriction.module,
            action=restriction.action,
            before={"module": restriction.module, "action": restriction.action},
            reason=cause,
        )

    # 1) DELETE exacto: solo los identificadores seleccionados y auditados. El
    #    recuento del modelo objetivo debe coincidir con los auditados.
    for model, ids in (
        (PermissionAssignment, assignment_ids),
        (PermissionRestriction, restriction_ids),
    ):
        _total, deleted = _delete_and_count(
            model.objects.filter(membership_id=membership.pk, pk__in=ids)
        )
        if deleted != len(ids):
            raise RuntimeError(
                "la limpieza de permisos no eliminó exactamente las filas auditadas."
            )

    # 2) DELETE residual por membresía. A diferencia de la lectura inicial, un
    #    DELETE opera sobre la versión confirmada más reciente de las filas, por
    #    lo que también detecta filas que una instantánea antigua (REPEATABLE
    #    READ) no mostró. Si elimina algo, esas filas no fueron auditadas: se
    #    lanza RuntimeError para que el rollback del llamador revierta ambos
    #    DELETE y los eventos registrados. No se audita ni se continúa.
    for model in (PermissionAssignment, PermissionRestriction):
        total, residual = _delete_and_count(
            model.objects.filter(membership_id=membership.pk)
        )
        if residual or total:
            raise RuntimeError(
                "la membresía conservaba permisos no auditados; se revierte la limpieza."
            )
    return len(assignment_ids), len(restriction_ids)


def _delete_and_count(queryset) -> tuple[int, int]:
    """Ejecuta ``delete()`` y retorna ``(total, filas del modelo objetivo)``.

    ``QuerySet.delete()`` devuelve un total que puede incluir cascadas y un
    diccionario por modelo; se usa el recuento de la etiqueta del modelo
    consultado, que Django omite cuando es cero.
    """
    total, per_model = queryset.delete()
    return total, per_model.get(queryset.model._meta.label, 0)
