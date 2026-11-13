# ruff: noqa: S101
"""Pruebas de F9: validación del motivo (reason) de auditoría.

Propósito: el motivo se recorta y no puede superar el límite de la columna
AuditEvent.reason (200). Se rechaza con un error claro, nunca se trunca, y un
rechazo dentro de una operación revierte la operación completa.
Contexto: las operaciones de invitaciones no reciben motivo; las entradas
reales son la revocación de integrantes (HTTP) y los comandos de capacidad.
"""
import pytest
from django.core.exceptions import ValidationError

from apps.accounts.errors import AppError
from apps.accounts.services.capability_service import set_farm_creation
from apps.farms import services
from apps.farms.models import FarmMembership, FarmRole, MembershipStatus
from apps.trazabilidad.models import AuditEvent, EventType
from apps.trazabilidad.services import (
    AUDIT_REASON_MAX_LENGTH,
    REASON_TOO_LONG,
    normalize_reason,
    record_audit_event,
)

pytestmark = pytest.mark.django_db


def _farm_with_operator(make_user):
    admin = make_user("11111111")
    farm_id = services.create_farm(user=admin, name="Finca").farm_id
    operator = make_user("22222222")
    membership = FarmMembership.objects.create(
        farm_id=farm_id, user=operator, role=FarmRole.OPERADOR
    )
    return admin, farm_id, membership


def _revoke(login, admin, farm_id, membership, reason):
    return login(admin).delete(
        f"/api/v1/farms/{farm_id}/members/{membership.pk}/",
        {"reason": reason},
        format="json",
    )


def _revoked_event(farm_id):
    return AuditEvent.objects.filter(farm_id=farm_id, event_type=EventType.MEMBER_REVOKED)


# --- Límite ----------------------------------------------------------------


def test_limit_comes_from_the_model_column():
    assert AUDIT_REASON_MAX_LENGTH == AuditEvent._meta.get_field("reason").max_length == 200
    assert REASON_TOO_LONG == "el motivo no puede superar 200 caracteres"


def test_reason_at_max_length_is_stored_complete(login, make_user):
    admin, farm_id, membership = _farm_with_operator(make_user)
    reason = "a" * AUDIT_REASON_MAX_LENGTH
    assert _revoke(login, admin, farm_id, membership, reason).status_code == 204
    assert _revoked_event(farm_id).get().reason == reason


def test_reason_over_max_length_rejected_before_operation(login, make_user):
    admin, farm_id, membership = _farm_with_operator(make_user)
    response = _revoke(login, admin, farm_id, membership, "a" * (AUDIT_REASON_MAX_LENGTH + 1))
    assert response.status_code == 400
    assert response.json() == {"error": REASON_TOO_LONG}
    membership.refresh_from_db()
    assert membership.status == MembershipStatus.ACTIVE
    assert not _revoked_event(farm_id).exists()


def test_empty_reason_is_allowed(login, make_user):
    admin, farm_id, membership = _farm_with_operator(make_user)
    assert _revoke(login, admin, farm_id, membership, "").status_code == 204
    assert _revoked_event(farm_id).get().reason == ""


def test_missing_reason_is_allowed(login, make_user):
    admin, farm_id, membership = _farm_with_operator(make_user)
    response = login(admin).delete(f"/api/v1/farms/{farm_id}/members/{membership.pk}/")
    assert response.status_code == 204
    assert _revoked_event(farm_id).get().reason == ""


def test_whitespace_only_reason_is_stored_empty(login, make_user):
    admin, farm_id, membership = _farm_with_operator(make_user)
    assert _revoke(login, admin, farm_id, membership, "   \t  ").status_code == 204
    assert _revoked_event(farm_id).get().reason == ""


def test_surrounding_spaces_are_trimmed_before_measuring(login, make_user):
    admin, farm_id, membership = _farm_with_operator(make_user)
    core = "b" * AUDIT_REASON_MAX_LENGTH
    assert _revoke(login, admin, farm_id, membership, f"   {core}   ").status_code == 204
    assert _revoked_event(farm_id).get().reason == core


def test_inner_spaces_and_accents_are_kept(login, make_user):
    admin, farm_id, membership = _farm_with_operator(make_user)
    reason = "Incumplió  la   norma: «riego» ñandú 🌱"
    assert _revoke(login, admin, farm_id, membership, reason).status_code == 204
    assert _revoked_event(farm_id).get().reason == reason


def test_multibyte_characters_count_as_characters(login, make_user):
    admin, farm_id, membership = _farm_with_operator(make_user)
    reason = "ñ" * (AUDIT_REASON_MAX_LENGTH - 1) + "🌱"
    assert _revoke(login, admin, farm_id, membership, reason).status_code == 204
    assert _revoked_event(farm_id).get().reason == reason


def test_multibyte_over_limit_rejected(login, make_user):
    admin, farm_id, membership = _farm_with_operator(make_user)
    response = _revoke(login, admin, farm_id, membership, "é" * (AUDIT_REASON_MAX_LENGTH + 1))
    assert response.status_code == 400


# --- Servicio central: sin truncamiento y con rollback ------------------------


def test_normalize_reason_rejects_instead_of_truncating():
    assert normalize_reason("  ok  ") == "ok"
    assert normalize_reason(None) == ""
    with pytest.raises(ValidationError) as exc:
        normalize_reason("x" * (AUDIT_REASON_MAX_LENGTH + 1))
    assert REASON_TOO_LONG in exc.value.messages


def test_record_audit_event_rejects_long_reason_without_writing():
    before = AuditEvent.objects.count()
    with pytest.raises(ValidationError):
        record_audit_event(
            event_type=EventType.MEMBER_REVOKED,
            entity_type="FarmMembership",
            entity_id="1",
            reason="x" * (AUDIT_REASON_MAX_LENGTH + 1),
        )
    assert AuditEvent.objects.count() == before


def test_service_long_reason_rolls_back_revocation(make_user):
    """Llamada directa al servicio (sin serializer): la revocación no se aplica."""
    admin, farm_id, membership = _farm_with_operator(make_user)
    with pytest.raises(ValidationError):
        services.revoke_member(
            user=admin,
            farm_id=farm_id,
            membership_id=membership.pk,
            reason="x" * (AUDIT_REASON_MAX_LENGTH + 1),
        )
    membership.refresh_from_db()
    assert membership.status == MembershipStatus.ACTIVE
    assert membership.revoked_at is None
    assert not _revoked_event(farm_id).exists()


def test_capability_command_rejects_long_reason_without_change(make_user):
    account = make_user("55555555", can_create_farms=False)
    with pytest.raises(AppError) as exc:
        set_farm_creation(
            identification="55555555",
            allowed=True,
            reason="x" * (AUDIT_REASON_MAX_LENGTH + 1),
        )
    assert exc.value.status_code == 400
    assert exc.value.message == REASON_TOO_LONG
    account.refresh_from_db()
    assert account.can_create_farms is False
    assert not AuditEvent.objects.filter(
        event_type=EventType.FARM_CREATION_GRANTED, entity_id=str(account.pk)
    ).exists()


# --- Invitaciones: no reciben motivo -------------------------------------------


def test_invitation_role_change_ignores_reason_field(login, make_user, monkeypatch):
    """InvitationRoleSerializer solo valida `role`; el motivo no llega a la auditoría."""
    from apps.accounts.integrations.delivery import DeliveryOutcome
    from apps.farms import invitation_services

    monkeypatch.setattr(
        invitation_services, "_send_invitation", lambda **kw: DeliveryOutcome.ACCEPTED
    )
    admin = make_user("11111111")
    farm_id = services.create_farm(user=admin, name="Finca").farm_id
    invitation = invitation_services.create_invitation(
        user=admin, farm_id=farm_id, email="x@example.com", role=FarmRole.OPERADOR
    )
    response = login(admin).patch(
        f"/api/v1/farms/{farm_id}/invitations/{invitation.pk}/",
        {"role": "consulta", "reason": "y" * 500},
        format="json",
    )
    assert response.status_code == 200
    events = AuditEvent.objects.filter(farm_id=farm_id, entity_type="FarmInvitation")
    assert events.exists()
    assert all(event.reason == "" for event in events)
