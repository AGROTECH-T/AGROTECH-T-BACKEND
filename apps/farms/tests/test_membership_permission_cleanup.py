# ruff: noqa: S101, S105, S106
"""Pruebas de A1: limpieza de permisos al salir, revocar o reactivar membresías.

Propósito: comprobar que una membresía que abandona la finca, es revocada o se
reactiva mediante invitación no conserva ni recupera asignaciones o
restricciones anteriores, con auditoría por fila, rollback completo y sin
afectar otras membresías ni fincas.

Contexto: requiere la configuración completa (``apps.permissions`` instalada y
sus tablas presentes). En la configuración aislada F2-2 el módulo se omite de
forma explícita; la compatibilidad F2-2 se valida reejecutando sus suites.
"""
import ast
import hashlib
from datetime import timedelta
from pathlib import Path
from threading import Event, Thread, current_thread
from types import SimpleNamespace

import pytest
from django.apps import apps as django_apps
from django.conf import settings as django_settings

# Solo se omite la configuración aislada coherente: limpieza desactivada de forma
# explícita y Permissions ausente. Con "required" (o la opción ausente) el módulo
# se ejecuta siempre, de modo que una configuración completa sin Permissions
# falla de forma visible en lugar de omitirse.
if (
    getattr(django_settings, "FARMS_PERMISSION_CLEANUP", "required") == "disabled"
    and not django_apps.is_installed("apps.permissions")
):
    pytest.skip(
        "FARMS_PERMISSION_CLEANUP='disabled' sin apps.permissions (F2-2 aislada).",
        allow_module_level=True,
    )

from django.core.exceptions import ImproperlyConfigured  # noqa: E402
from django.db import connections, transaction  # noqa: E402
from django.db.models.query import QuerySet  # noqa: E402
from django.utils import timezone  # noqa: E402

from apps.accounts.errors import AppError  # noqa: E402
from apps.farms import invitation_services, services  # noqa: E402
from apps.farms.models import (  # noqa: E402
    FarmInvitation,
    FarmMembership,
    FarmRole,
    InvitationStatus,
    MembershipStatus,
)
from apps.permissions import services as permission_services  # noqa: E402
from apps.permissions.models import (  # noqa: E402
    ApprovalPolicy,
    AssignmentSource,
    PermissionAssignment,
    PermissionRestriction,
)
from apps.trazabilidad.models import AuditEvent, EventType  # noqa: E402

pytestmark = pytest.mark.django_db

CLEANUP_EVENT_TYPES = (EventType.PERMISSION_REVOKED, EventType.PERMISSION_UNRESTRICTED)


# --- Utilidades ---


def _farm(admin):
    return services.create_farm(user=admin, name="Finca").farm_id


def _member(farm_id, user, role=FarmRole.OPERADOR):
    return FarmMembership.objects.create(farm_id=farm_id, user=user, role=role)


def _assign(membership, module="inventario", action="crear", source=AssignmentSource.ADMIN):
    """Inserta una asignación directamente, sin depender de los servicios."""
    return PermissionAssignment.objects.create(
        membership=membership, module=module, action=action, source=source
    )


def _restrict(membership, module="inventario", action="consultar"):
    """Inserta una restricción directamente, sin depender de los servicios."""
    return PermissionRestriction.objects.create(
        membership=membership, module=module, action=action
    )


def _rows(membership):
    return (
        PermissionAssignment.objects.filter(membership_id=membership.pk).count(),
        PermissionRestriction.objects.filter(membership_id=membership.pk).count(),
    )


def _cleanup_events(membership, event_type=None, reason=None):
    queryset = AuditEvent.objects.filter(
        entity_type="FarmMembership",
        entity_id=str(membership.pk),
        event_type__in=CLEANUP_EVENT_TYPES,
    )
    if event_type is not None:
        queryset = queryset.filter(event_type=event_type)
    if reason is not None:
        queryset = queryset.filter(reason=reason)
    return queryset.order_by("id")


def _sha(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _invite(farm_id, email, token, role=FarmRole.OPERADOR):
    return FarmInvitation.objects.create(
        farm_id=farm_id,
        email=email,
        pending_email=email,
        role=role,
        status=InvitationStatus.PENDING,
        token_hash=_sha(token),
        expires_at=timezone.now() + timedelta(days=7),
    )


def _exit(exit_operation, *, admin, member_user, farm_id, membership):
    if exit_operation == "left":
        return services.leave_farm(user=member_user, farm_id=farm_id)
    return services.revoke_member(
        user=admin, farm_id=farm_id, membership_id=membership.pk
    )


def _without_permissions_app(monkeypatch):
    """Simula que apps.permissions no está instalada solo para apps.farms.services."""
    monkeypatch.setattr(
        services,
        "django_apps",
        SimpleNamespace(is_installed=lambda name: name != services.PERMISSIONS_APP),
    )


class _FarmLockProbe:
    """Señales que distinguen intento y adquisición real del bloqueo de ``Farm``."""

    def __init__(self):
        self.holder_locked = Event()
        self.release_holder = Event()
        self.waiter_attempted = Event()
        self.waiter_acquired = Event()


def _probe_farm_lock(monkeypatch, *, waiter, holder=None):
    """Envuelve ``services._lock_farm`` según el nombre del hilo.

    - ``holder``: adquiere el bloqueo, lo señala y lo retiene hasta su liberación.
    - ``waiter``: señala el intento justo antes de invocar el bloqueo original y
      la adquisición solo cuando este retorna.

    ``leave_farm`` (vía ``_lock_membership``), ``revoke_member`` (vía
    ``_lock_farm_with_memberships``) y ``accept_invitation`` (vía
    ``_lock_active_farm``) resuelven ``_lock_farm`` en tiempo de llamada.
    """
    probe = _FarmLockProbe()
    original_lock = services._lock_farm

    def instrumented_lock(*, farm_id):
        name = current_thread().name
        if holder is not None and name.startswith(holder):
            farm = original_lock(farm_id=farm_id)
            probe.holder_locked.set()
            if not probe.release_holder.wait(timeout=10):
                raise RuntimeError("el hilo que retiene el bloqueo no fue liberado")
            return farm
        if name.startswith(waiter):
            probe.waiter_attempted.set()
            farm = original_lock(farm_id=farm_id)
            probe.waiter_acquired.set()
            return farm
        return original_lock(farm_id=farm_id)

    monkeypatch.setattr(services, "_lock_farm", instrumented_lock)
    return probe


def _run_in_thread(name, operation, results):
    """Ejecuta una operación en un hilo con conexión propia y guarda su resultado."""

    def target():
        try:
            results[name] = operation()
        except Exception as exc:  # se conserva para comprobarlo en el hilo principal
            results[name] = exc
        finally:
            connections.close_all()

    return Thread(target=target, name=name)


# --- Salida y revocación ---


def test_leave_clears_assignments_with_individual_audit(make_user):
    admin = make_user("11111111")
    farm_id = _farm(admin)
    member_user = make_user("22222222")
    membership = _member(farm_id, member_user)
    _assign(membership, "inventario", "crear", AssignmentSource.ADMIN)
    _assign(membership, "inventario", "consultar", AssignmentSource.INVITATION)

    services.leave_farm(user=member_user, farm_id=farm_id)

    membership.refresh_from_db()
    assert membership.status == MembershipStatus.LEFT
    assert _rows(membership) == (0, 0)
    events = list(_cleanup_events(membership, EventType.PERMISSION_REVOKED))
    assert len(events) == 2
    assert {
        (e.before_data["module"], e.before_data["action"], e.before_data["source"])
        for e in events
    } == {
        ("inventario", "crear", AssignmentSource.ADMIN),
        ("inventario", "consultar", AssignmentSource.INVITATION),
    }
    for event in events:
        assert event.reason == "member_left"
        assert event.actor_id == member_user.pk
        assert event.farm_id == farm_id
        assert event.metadata["membership_id"] == membership.pk
    assert AuditEvent.objects.filter(
        farm_id=farm_id, event_type=EventType.MEMBER_LEFT
    ).exists()


def test_leave_clears_restrictions_with_individual_audit(make_user):
    admin = make_user("11111111")
    farm_id = _farm(admin)
    member_user = make_user("22222222")
    membership = _member(farm_id, member_user)
    _restrict(membership, "inventario", "consultar")

    services.leave_farm(user=member_user, farm_id=farm_id)

    assert _rows(membership) == (0, 0)
    events = list(_cleanup_events(membership, EventType.PERMISSION_UNRESTRICTED))
    assert len(events) == 1
    assert events[0].before_data == {"module": "inventario", "action": "consultar"}
    assert events[0].reason == "member_left"
    assert events[0].actor_id == member_user.pk


def test_revoke_clears_assignments_and_restrictions(make_user):
    admin = make_user("11111111")
    farm_id = _farm(admin)
    member_user = make_user("22222222")
    membership = _member(farm_id, member_user)
    _assign(membership)
    _restrict(membership)

    services.revoke_member(user=admin, farm_id=farm_id, membership_id=membership.pk)

    membership.refresh_from_db()
    assert membership.status == MembershipStatus.REVOKED
    assert _rows(membership) == (0, 0)
    revoked = list(_cleanup_events(membership, EventType.PERMISSION_REVOKED))
    unrestricted = list(_cleanup_events(membership, EventType.PERMISSION_UNRESTRICTED))
    assert len(revoked) == 1
    assert len(unrestricted) == 1
    for event in revoked + unrestricted:
        assert event.reason == "member_revoked"
        assert event.actor_id == admin.pk
    assert AuditEvent.objects.filter(
        farm_id=farm_id, event_type=EventType.MEMBER_REVOKED
    ).exists()


def test_leave_rejected_for_last_admin_keeps_permissions(make_user):
    admin = make_user("11111111")
    farm_id = _farm(admin)
    admin_membership = FarmMembership.objects.get(farm_id=farm_id, user=admin)
    _assign(admin_membership)
    _restrict(admin_membership)

    with pytest.raises(AppError) as exc:
        services.leave_farm(user=admin, farm_id=farm_id)

    assert exc.value.status_code == 409
    assert _rows(admin_membership) == (1, 1)
    assert not _cleanup_events(admin_membership).exists()


# --- Reactivación mediante invitación ---


@pytest.mark.parametrize("exit_operation", ["left", "revoked"])
def test_accept_reactivation_drops_legacy_permissions(make_user, exit_operation):
    admin = make_user("11111111")
    farm_id = _farm(admin)
    invited = make_user("22222222", correo="invited@example.com", email_verified=True)
    membership = _member(farm_id, invited)
    _exit(exit_operation, admin=admin, member_user=invited, farm_id=farm_id,
          membership=membership)
    # Filas antiguas insertadas directamente: simulan datos previos a A1.
    _assign(membership)
    _restrict(membership)
    _invite(farm_id, "invited@example.com", token="tok")

    result = invitation_services.accept_invitation(user=invited, token="tok")

    assert result.pk == membership.pk
    result.refresh_from_db()
    assert result.status == MembershipStatus.ACTIVE
    assert _rows(result) == (0, 0)
    assert permission_services.assigned(result) == frozenset()
    assert permission_services.restricted(result) == frozenset()
    events = list(_cleanup_events(result, reason="member_reactivated"))
    assert {e.event_type for e in events} == set(CLEANUP_EVENT_TYPES)
    assert len(events) == 2
    assert all(e.actor_id == invited.pk for e in events)
    assert AuditEvent.objects.filter(
        farm_id=farm_id, event_type=EventType.MEMBER_REACTIVATED
    ).exists()


def test_accept_new_membership_has_no_cleanup_events(make_user):
    admin = make_user("11111111")
    farm_id = _farm(admin)
    invited = make_user("22222222", correo="invited@example.com", email_verified=True)
    _invite(farm_id, "invited@example.com", token="tok")

    membership = invitation_services.accept_invitation(user=invited, token="tok")

    assert _rows(membership) == (0, 0)
    assert not AuditEvent.objects.filter(
        farm_id=farm_id, event_type__in=CLEANUP_EVENT_TYPES
    ).exists()
    assert AuditEvent.objects.filter(
        farm_id=farm_id, event_type=EventType.MEMBER_JOINED
    ).exists()


def test_accept_failed_validation_does_not_clean(make_user):
    admin = make_user("11111111")
    farm_id = _farm(admin)
    invited = make_user("22222222", correo="invited@example.com", email_verified=False)
    membership = _member(farm_id, invited)
    services.leave_farm(user=invited, farm_id=farm_id)
    _assign(membership)
    _restrict(membership)
    _invite(farm_id, "invited@example.com", token="tok")

    with pytest.raises(AppError) as exc:
        invitation_services.accept_invitation(user=invited, token="tok")

    assert exc.value.status_code == 404
    membership.refresh_from_db()
    assert membership.status == MembershipStatus.LEFT
    assert _rows(membership) == (1, 1)
    assert not _cleanup_events(membership, reason="member_reactivated").exists()


def test_accept_active_member_conflict_does_not_clean(make_user):
    admin = make_user("11111111")
    farm_id = _farm(admin)
    invited = make_user("22222222", correo="invited@example.com", email_verified=True)
    membership = _member(farm_id, invited)
    _assign(membership)
    _invite(farm_id, "invited@example.com", token="tok")

    with pytest.raises(AppError) as exc:
        invitation_services.accept_invitation(user=invited, token="tok")

    assert exc.value.status_code == 409
    assert _rows(membership) == (1, 0)
    assert not _cleanup_events(membership).exists()


# --- Historial y aislamiento ---


def test_cleanup_preserves_previous_audit_history(make_user):
    admin = make_user("11111111")
    farm_id = _farm(admin)
    member_user = make_user("22222222")
    membership = _member(farm_id, member_user)
    permission_services.grant(admin, membership, "inventario", "crear")
    granted = AuditEvent.objects.get(
        farm_id=farm_id, event_type=EventType.PERMISSION_GRANTED
    )
    snapshot = (granted.before_data, granted.after_data, granted.reason, granted.metadata)
    previous_ids = set(AuditEvent.objects.filter(farm_id=farm_id).values_list("pk", flat=True))

    services.leave_farm(user=member_user, farm_id=farm_id)

    granted.refresh_from_db()
    assert (
        granted.before_data,
        granted.after_data,
        granted.reason,
        granted.metadata,
    ) == snapshot
    current_ids = set(AuditEvent.objects.filter(farm_id=farm_id).values_list("pk", flat=True))
    assert previous_ids <= current_ids


def test_cleanup_is_scoped_to_the_affected_membership(make_user):
    admin = make_user("11111111")
    farm_id = _farm(admin)
    other_admin = make_user("33333333")
    other_farm_id = _farm(other_admin)
    member_user = make_user("22222222")
    target = _member(farm_id, member_user)
    same_user_other_farm = _member(other_farm_id, member_user)
    other_member = _member(farm_id, make_user("44444444"))
    for membership in (target, same_user_other_farm, other_member):
        _assign(membership)
        _restrict(membership)
    policy = ApprovalPolicy.objects.create(farm_id=farm_id, module="inventario")

    services.revoke_member(user=admin, farm_id=farm_id, membership_id=target.pk)

    assert _rows(target) == (0, 0)
    assert _rows(same_user_other_farm) == (1, 1)
    assert _rows(other_member) == (1, 1)
    assert ApprovalPolicy.objects.filter(pk=policy.pk).exists()
    assert not _cleanup_events(same_user_other_farm).exists()
    assert not _cleanup_events(other_member).exists()


# --- Atomicidad ---


def test_delete_failure_rolls_back_revocation(make_user, monkeypatch):
    admin = make_user("11111111")
    farm_id = _farm(admin)
    membership = _member(farm_id, make_user("22222222"))
    _assign(membership)
    _restrict(membership)

    def boom(self, *args, **kwargs):
        raise RuntimeError("fallo de eliminación")

    monkeypatch.setattr(QuerySet, "delete", boom)

    with pytest.raises(RuntimeError):
        services.revoke_member(user=admin, farm_id=farm_id, membership_id=membership.pk)

    monkeypatch.undo()
    membership.refresh_from_db()
    assert membership.status == MembershipStatus.ACTIVE
    assert _rows(membership) == (1, 1)
    assert not _cleanup_events(membership).exists()
    assert not AuditEvent.objects.filter(
        farm_id=farm_id, event_type=EventType.MEMBER_REVOKED
    ).exists()


def test_audit_failure_rolls_back_leave(make_user, monkeypatch):
    admin = make_user("11111111")
    farm_id = _farm(admin)
    member_user = make_user("22222222")
    membership = _member(farm_id, member_user)
    _assign(membership)
    _restrict(membership)

    def boom(**kwargs):
        raise RuntimeError("fallo de auditoría")

    monkeypatch.setattr("apps.permissions.services.record_audit_event", boom)

    with pytest.raises(RuntimeError):
        services.leave_farm(user=member_user, farm_id=farm_id)

    membership.refresh_from_db()
    assert membership.status == MembershipStatus.ACTIVE
    assert _rows(membership) == (1, 1)
    assert not AuditEvent.objects.filter(
        farm_id=farm_id, event_type=EventType.MEMBER_LEFT
    ).exists()


def test_audit_failure_rolls_back_reactivation(make_user, monkeypatch):
    admin = make_user("11111111")
    farm_id = _farm(admin)
    invited = make_user("22222222", correo="invited@example.com", email_verified=True)
    membership = _member(farm_id, invited)
    services.leave_farm(user=invited, farm_id=farm_id)
    _assign(membership)
    _restrict(membership)
    _invite(farm_id, "invited@example.com", token="tok")

    def boom(**kwargs):
        raise RuntimeError("fallo de auditoría")

    monkeypatch.setattr("apps.permissions.services.record_audit_event", boom)

    with pytest.raises(RuntimeError):
        invitation_services.accept_invitation(user=invited, token="tok")

    membership.refresh_from_db()
    assert membership.status == MembershipStatus.LEFT
    assert _rows(membership) == (1, 1)
    invitation = FarmInvitation.objects.get(farm_id=farm_id)
    assert invitation.status == InvitationStatus.PENDING
    assert invitation.token_hash == _sha("tok")
    assert not AuditEvent.objects.filter(
        farm_id=farm_id,
        event_type__in=[EventType.MEMBER_REACTIVATED, EventType.INVITATION_ACCEPTED],
    ).exists()


# --- Idempotencia y contrato de la función ---


def test_cleanup_is_idempotent(make_user):
    admin = make_user("11111111")
    farm_id = _farm(admin)
    invited = make_user("22222222", correo="invited@example.com", email_verified=True)
    membership = _member(farm_id, invited)
    _assign(membership)
    _restrict(membership)

    with transaction.atomic():
        first = permission_services.clear_membership_permissions(
            membership=membership, actor=admin, cause="member_revoked"
        )
        second = permission_services.clear_membership_permissions(
            membership=membership, actor=admin, cause="member_revoked"
        )
    assert first == (1, 1)
    assert second == (0, 0)
    assert _cleanup_events(membership).count() == 2

    services.leave_farm(user=invited, farm_id=farm_id)
    _invite(farm_id, "invited@example.com", token="tok")
    invitation_services.accept_invitation(user=invited, token="tok")
    services.leave_farm(user=invited, farm_id=farm_id)

    assert _rows(membership) == (0, 0)
    assert _cleanup_events(membership).count() == 2


def test_clear_rejects_unknown_cause(make_user):
    admin = make_user("11111111")
    farm_id = _farm(admin)
    membership = _member(farm_id, make_user("22222222"))
    _assign(membership)

    with pytest.raises(ValueError):
        permission_services.clear_membership_permissions(
            membership=membership, actor=admin, cause="otra_causa"
        )

    assert _rows(membership) == (1, 0)
    assert not _cleanup_events(membership).exists()


def test_role_change_and_transfer_keep_permissions(make_user):
    admin = make_user("11111111")
    farm_id = _farm(admin)
    admin_membership = FarmMembership.objects.get(farm_id=farm_id, user=admin)
    membership = _member(farm_id, make_user("22222222"))
    _assign(membership)
    _restrict(membership)
    _assign(admin_membership)

    services.change_member_role(
        user=admin, farm_id=farm_id, membership_id=membership.pk, role=FarmRole.CONSULTA
    )
    services.change_member_role(
        user=admin, farm_id=farm_id, membership_id=membership.pk, role=FarmRole.OPERADOR
    )
    services.transfer_admin(user=admin, farm_id=farm_id, membership_id=membership.pk)

    assert _rows(membership) == (1, 1)
    assert _rows(admin_membership) == (1, 0)
    assert not AuditEvent.objects.filter(
        farm_id=farm_id, event_type__in=CLEANUP_EVENT_TYPES
    ).exists()


# --- Configuración explícita FARMS_PERMISSION_CLEANUP ---


def test_config_required_with_permissions_installed_cleans(make_user, settings):
    settings.FARMS_PERMISSION_CLEANUP = "required"
    admin = make_user("11111111")
    farm_id = _farm(admin)
    member_user = make_user("22222222")
    membership = _member(farm_id, member_user)
    _assign(membership)

    services.leave_farm(user=member_user, farm_id=farm_id)

    assert _rows(membership) == (0, 0)
    assert _cleanup_events(membership, reason="member_left").count() == 1


def test_config_missing_setting_defaults_to_required(make_user, settings):
    del settings.FARMS_PERMISSION_CLEANUP
    assert not hasattr(django_settings, "FARMS_PERMISSION_CLEANUP")
    admin = make_user("11111111")
    farm_id = _farm(admin)
    member_user = make_user("22222222")
    membership = _member(farm_id, member_user)
    _assign(membership)

    services.leave_farm(user=member_user, farm_id=farm_id)

    assert _rows(membership) == (0, 0)


def test_config_required_without_permissions_raises_and_rolls_back(
    make_user, settings, monkeypatch
):
    settings.FARMS_PERMISSION_CLEANUP = "required"
    _without_permissions_app(monkeypatch)
    admin = make_user("11111111")
    farm_id = _farm(admin)
    member_user = make_user("22222222")
    membership = _member(farm_id, member_user)
    _assign(membership)

    with pytest.raises(ImproperlyConfigured):
        services.leave_farm(user=member_user, farm_id=farm_id)

    membership.refresh_from_db()
    assert membership.status == MembershipStatus.ACTIVE
    assert _rows(membership) == (1, 0)
    assert not AuditEvent.objects.filter(
        farm_id=farm_id, event_type=EventType.MEMBER_LEFT
    ).exists()


def test_config_disabled_without_permissions_skips_cleanup(
    make_user, settings, monkeypatch
):
    settings.FARMS_PERMISSION_CLEANUP = "disabled"
    _without_permissions_app(monkeypatch)
    admin = make_user("11111111")
    farm_id = _farm(admin)
    member_user = make_user("22222222")
    membership = _member(farm_id, member_user)
    _assign(membership)
    _restrict(membership)

    services.leave_farm(user=member_user, farm_id=farm_id)

    membership.refresh_from_db()
    assert membership.status == MembershipStatus.LEFT
    # Las tablas existen en la configuración completa; el modo desactivado no las toca.
    assert _rows(membership) == (1, 1)
    assert not _cleanup_events(membership).exists()


def test_config_disabled_with_permissions_installed_raises(make_user, settings):
    settings.FARMS_PERMISSION_CLEANUP = "disabled"
    admin = make_user("11111111")
    farm_id = _farm(admin)
    membership = _member(farm_id, make_user("22222222"))
    _assign(membership)

    with pytest.raises(ImproperlyConfigured):
        services.revoke_member(user=admin, farm_id=farm_id, membership_id=membership.pk)

    membership.refresh_from_db()
    assert membership.status == MembershipStatus.ACTIVE
    assert _rows(membership) == (1, 0)


def test_config_disabled_with_permissions_installed_raises_on_accept(make_user, settings):
    settings.FARMS_PERMISSION_CLEANUP = "disabled"
    admin = make_user("11111111")
    farm_id = _farm(admin)
    invited = make_user("22222222", correo="invited@example.com", email_verified=True)
    membership = _member(farm_id, invited)
    membership.status = MembershipStatus.LEFT
    membership.save(update_fields=["status", "updated_at"])
    _assign(membership)
    _invite(farm_id, "invited@example.com", token="tok")

    with pytest.raises(ImproperlyConfigured):
        invitation_services.accept_invitation(user=invited, token="tok")

    membership.refresh_from_db()
    assert membership.status == MembershipStatus.LEFT
    assert _rows(membership) == (1, 0)
    assert FarmInvitation.objects.get(farm_id=farm_id).status == InvitationStatus.PENDING


@pytest.mark.parametrize("value", ["", "Required", "off", None])
def test_config_invalid_value_raises(make_user, settings, value):
    settings.FARMS_PERMISSION_CLEANUP = value
    admin = make_user("11111111")
    farm_id = _farm(admin)
    member_user = make_user("22222222")
    membership = _member(farm_id, member_user)
    _assign(membership)

    with pytest.raises(ImproperlyConfigured):
        services.leave_farm(user=member_user, farm_id=farm_id)

    membership.refresh_from_db()
    assert membership.status == MembershipStatus.ACTIVE
    assert _rows(membership) == (1, 0)


def test_full_settings_require_cleanup():
    assert django_settings.FARMS_PERMISSION_CLEANUP == "required"
    assert django_apps.is_installed("apps.permissions")
    assert services._permission_cleanup_enabled() is True


def test_isolated_settings_declare_cleanup_disabled():
    """F2-2: la configuración aislada desactiva la limpieza de forma explícita.

    Se analiza el código fuente sin importarlo para no activar las
    protecciones de la base desechable. La ejecución real se valida en F2-2.
    """
    source = Path(django_settings.BASE_DIR, "config", "settings_test_disposable_isolated.py")
    tree = ast.parse(source.read_text(encoding="utf-8"))
    values = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    values[target.id] = node.value
    assert ast.literal_eval(values["FARMS_PERMISSION_CLEANUP"]) == "disabled"
    assert "apps.permissions" not in ast.literal_eval(values["INSTALLED_APPS"])


# --- Concurrencia (MariaDB con transacciones reales) ---


@pytest.mark.django_db(transaction=True)
def test_concurrent_accept_reactivation_cleans_once(make_user, monkeypatch):
    """T10: dos aceptaciones solapadas; una gana, la otra espera y recibe 404.

    La primera aceptación adquiere el bloqueo de ``Farm`` y lo retiene. La
    segunda, en otra conexión, ya resolvió la invitación (todavía pendiente para
    ella) y llega al punto exacto de solicitar el mismo bloqueo; se comprueba que
    no lo adquiere mientras la primera lo retiene. Al liberarla, la primera
    confirma y la segunda obtiene el bloqueo, revalida la invitación ya aceptada
    y responde 404 sin repetir la limpieza.

    Alcance de la instrumentación: solo se instrumenta el bloqueo de ``Farm``,
    con lo que se demuestra que la segunda aceptación espera mientras la primera
    lo retiene. Los bloqueos posteriores de membresía e invitación no se miden
    individualmente; se adquieren después, en el orden de ejecución de
    ``accept_invitation``, ya serializados por el bloqueo de ``Farm``.

    El contrato de error es ``{"error": mensaje}`` sin código estable, y
    ``accept_invitation`` usa el mismo mensaje para todo 404 de aceptación. El
    mensaje descarta otros 404 (por ejemplo, ``finca no encontrada``) y el
    estado final de la invitación confirma que el motivo es la aceptación previa.
    """
    admin = make_user("11111111")
    farm_id = _farm(admin)
    invited = make_user("22222222", correo="invited@example.com", email_verified=True)
    membership = _member(farm_id, invited)
    services.revoke_member(user=admin, farm_id=farm_id, membership_id=membership.pk)
    _assign(membership)
    _restrict(membership)
    token = "tok-123"
    _invite(farm_id, "invited@example.com", token=token)

    probe = _probe_farm_lock(monkeypatch, holder="acceptor-first", waiter="acceptor-second")
    results = {}

    def accept():
        return invitation_services.accept_invitation(user=invited, token=token)

    first = _run_in_thread("acceptor-first", accept, results)
    second = _run_in_thread("acceptor-second", accept, results)
    try:
        first.start()
        assert probe.holder_locked.wait(timeout=5)
        second.start()
        assert probe.waiter_attempted.wait(timeout=5)
        acquired_while_held = probe.waiter_acquired.wait(timeout=1)
    finally:
        probe.release_holder.set()
        first.join(timeout=15)
        if second.ident is not None:
            second.join(timeout=15)

    assert not acquired_while_held
    assert probe.waiter_acquired.is_set()
    assert not first.is_alive()
    assert not second.is_alive()
    assert isinstance(results["acceptor-first"], FarmMembership)
    second_result = results["acceptor-second"]
    assert isinstance(second_result, AppError)
    assert second_result.status_code == 404
    assert second_result.message == invitation_services.INVITATION_NOT_FOUND

    invitation = FarmInvitation.objects.get(farm_id=farm_id)
    assert invitation.status == InvitationStatus.ACCEPTED
    assert invitation.token_hash == ""

    membership.refresh_from_db()
    assert membership.status == MembershipStatus.ACTIVE
    assert _rows(membership) == (0, 0)
    reactivation_events = _cleanup_events(membership, reason="member_reactivated")
    assert reactivation_events.filter(event_type=EventType.PERMISSION_REVOKED).count() == 1
    assert (
        reactivation_events.filter(event_type=EventType.PERMISSION_UNRESTRICTED).count()
        == 1
    )
    assert (
        AuditEvent.objects.filter(
            farm_id=farm_id, event_type=EventType.MEMBER_REACTIVATED
        ).count()
        == 1
    )
    assert (
        AuditEvent.objects.filter(
            farm_id=farm_id, event_type=EventType.INVITATION_ACCEPTED
        ).count()
        == 1
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("exit_operation", "cause", "final_status"),
    [
        ("left", "member_left", MembershipStatus.LEFT),
        ("revoked", "member_revoked", MembershipStatus.REVOKED),
    ],
)
def test_concurrent_grant_and_exit_leave_no_assignment(
    make_user, monkeypatch, exit_operation, cause, final_status
):
    """T10b: la salida o revocación espera a una concesión en curso y luego la limpia.

    A. La concesión adquiere el bloqueo de ``Farm`` (en ``_lock_admin_and_target``)
       y se detiene en ``_require_ceiling`` reteniéndolo.
    B. La transición, en otra conexión, señala el intento justo antes de invocar
       ``services._lock_farm`` y no lo adquiere mientras la concesión lo retiene.
    C. Al liberar la concesión, esta confirma y la transición adquiere el bloqueo,
       ve la asignación confirmada y la elimina con auditoría.
    """
    admin = make_user("11111111")
    farm_id = _farm(admin)
    member_user = make_user("22222222")
    membership = _member(farm_id, member_user)

    original_require_ceiling = permission_services._require_ceiling
    grant_locked = Event()
    release_grant = Event()
    results = {}

    def instrumented_require_ceiling(target, permission):
        if current_thread().name.startswith("granter"):
            grant_locked.set()
            if not release_grant.wait(timeout=10):
                raise RuntimeError("la concesión no fue liberada")
        return original_require_ceiling(target, permission)

    monkeypatch.setattr(
        permission_services, "_require_ceiling", instrumented_require_ceiling
    )
    probe = _probe_farm_lock(monkeypatch, waiter="exiter")

    def grant():
        return permission_services.grant(admin, membership, "inventario", "crear")

    def exit_membership():
        return _exit(
            exit_operation,
            admin=admin,
            member_user=member_user,
            farm_id=farm_id,
            membership=membership,
        )

    grant_thread = _run_in_thread("granter", grant, results)
    exit_thread = _run_in_thread("exiter", exit_membership, results)
    try:
        grant_thread.start()
        assert grant_locked.wait(timeout=5)
        exit_thread.start()
        assert probe.waiter_attempted.wait(timeout=5)
        acquired_while_granting = probe.waiter_acquired.wait(timeout=1)
    finally:
        release_grant.set()
        grant_thread.join(timeout=15)
        if exit_thread.ident is not None:
            exit_thread.join(timeout=15)

    assert not acquired_while_granting
    assert probe.waiter_acquired.is_set()
    assert not grant_thread.is_alive()
    assert not exit_thread.is_alive()
    assert isinstance(results["granter"], PermissionAssignment)
    assert isinstance(results["exiter"], FarmMembership)

    membership.refresh_from_db()
    assert membership.status == final_status
    assert _rows(membership) == (0, 0)
    assert (
        AuditEvent.objects.filter(
            farm_id=farm_id, event_type=EventType.PERMISSION_GRANTED
        ).count()
        == 1
    )
    assert _cleanup_events(membership, EventType.PERMISSION_REVOKED, cause).count() == 1


@pytest.mark.parametrize("exit_operation", ["left", "revoked"])
def test_grant_after_exit_is_rejected(make_user, exit_operation):
    """Orden inverso de T10b: tras la transición confirmada no se puede conceder."""
    admin = make_user("11111111")
    farm_id = _farm(admin)
    member_user = make_user("22222222")
    membership = _member(farm_id, member_user)
    _exit(exit_operation, admin=admin, member_user=member_user, farm_id=farm_id,
          membership=membership)

    with pytest.raises(AppError) as exc:
        permission_services.grant(admin, membership, "inventario", "crear")

    assert exc.value.status_code == 404
    assert _rows(membership) == (0, 0)
