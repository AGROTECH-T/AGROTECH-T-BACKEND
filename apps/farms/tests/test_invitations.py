# ruff: noqa: S101
"""Pruebas de los servicios de invitaciones a fincas (FARMS-108)."""
import hashlib
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from django.db import connections
from django.utils import timezone

from apps.accounts.errors import AppError
from apps.accounts.integrations.delivery import DeliveryOutcome
from apps.farms import invitation_services, services
from apps.farms.models import (
    FarmInvitation,
    FarmMembership,
    FarmRole,
    InvitationStatus,
    MembershipStatus,
)
from apps.trazabilidad.models import AuditEvent, EventType

pytestmark = pytest.mark.django_db


def _stub_delivery(monkeypatch, outcome=DeliveryOutcome.ACCEPTED):
    monkeypatch.setattr(
        "apps.farms.invitation_services._send_invitation", lambda **kw: outcome
    )


def _farm(admin):
    return services.create_farm(user=admin, name="Finca").farm_id


def _sha(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _invite(farm_id, email, role=FarmRole.OPERADOR, token="tok", admin=None):
    return FarmInvitation.objects.create(
        farm_id=farm_id,
        email=email,
        pending_email=email,
        role=role,
        status=InvitationStatus.PENDING,
        token_hash=_sha(token),
        expires_at=timezone.now() + timedelta(days=7),
        invited_by=admin,
    )


def _event(farm_id, event_type):
    return AuditEvent.objects.filter(farm_id=farm_id, event_type=event_type).first()


# --- Creación ---


def test_create_invitation_normalizes_email(make_user, monkeypatch):
    _stub_delivery(monkeypatch)
    admin = make_user("11111111")
    farm_id = _farm(admin)
    inv = invitation_services.create_invitation(
        user=admin, farm_id=farm_id, email="  X@Example.COM ", role=FarmRole.OPERADOR
    )
    assert inv.email == "x@example.com"
    assert inv.pending_email == "x@example.com"
    assert inv.status == InvitationStatus.PENDING
    assert inv.token_hash
    assert _event(farm_id, EventType.INVITATION_CREATED) is not None


def test_create_invitation_requires_admin(make_user, monkeypatch):
    _stub_delivery(monkeypatch)
    admin = make_user("11111111")
    farm_id = _farm(admin)
    operator = make_user("22222222")
    FarmMembership.objects.create(farm_id=farm_id, user=operator, role=FarmRole.OPERADOR)
    with pytest.raises(AppError) as exc:
        invitation_services.create_invitation(
            user=operator, farm_id=farm_id, email="x@example.com", role=FarmRole.OPERADOR
        )
    assert exc.value.status_code == 403


def test_create_duplicate_pending_conflict(make_user, monkeypatch):
    _stub_delivery(monkeypatch)
    admin = make_user("11111111")
    farm_id = _farm(admin)
    invitation_services.create_invitation(
        user=admin, farm_id=farm_id, email="x@example.com", role=FarmRole.OPERADOR
    )
    with pytest.raises(AppError) as exc:
        invitation_services.create_invitation(
            user=admin, farm_id=farm_id, email="x@example.com", role=FarmRole.CONSULTA
        )
    assert exc.value.status_code == 409


def test_create_rolls_back_on_audit_failure(make_user, monkeypatch):
    _stub_delivery(monkeypatch)
    admin = make_user("11111111")
    farm_id = _farm(admin)

    def fail(*args, **kwargs):
        raise RuntimeError("auditoría falló")

    monkeypatch.setattr(invitation_services, "record_audit_event", fail)
    with pytest.raises(RuntimeError):
        invitation_services.create_invitation(
            user=admin, farm_id=farm_id, email="x@example.com", role=FarmRole.OPERADOR
        )
    assert not FarmInvitation.objects.filter(farm_id=farm_id).exists()


# --- Reenvío ---


def test_resend_rotates_token_and_renews_expiry(make_user, monkeypatch):
    _stub_delivery(monkeypatch)
    admin = make_user("11111111")
    farm_id = _farm(admin)
    inv = invitation_services.create_invitation(
        user=admin, farm_id=farm_id, email="x@example.com", role=FarmRole.OPERADOR
    )
    old_hash = inv.token_hash
    FarmInvitation.objects.filter(pk=inv.pk).update(
        expires_at=timezone.now() + timedelta(hours=1)
    )
    old_expiry = FarmInvitation.objects.get(pk=inv.pk).expires_at

    resend = invitation_services.resend_invitation(
        user=admin, farm_id=farm_id, invitation_id=inv.pk
    )
    resend.refresh_from_db()
    assert resend.token_hash != old_hash
    assert resend.expires_at > old_expiry
    assert _event(farm_id, EventType.INVITATION_RESEND_STARTED) is not None
    assert _event(farm_id, EventType.INVITATION_RESENT) is not None


def test_resend_non_pending_rejected(make_user, monkeypatch):
    _stub_delivery(monkeypatch)
    admin = make_user("11111111")
    farm_id = _farm(admin)
    inv = invitation_services.create_invitation(
        user=admin, farm_id=farm_id, email="x@example.com", role=FarmRole.OPERADOR
    )
    invitation_services.cancel_invitation(user=admin, farm_id=farm_id, invitation_id=inv.pk)
    with pytest.raises(AppError) as exc:
        invitation_services.resend_invitation(user=admin, farm_id=farm_id, invitation_id=inv.pk)
    assert exc.value.status_code == 409


# --- Cambio de rol ---


def test_change_invitation_role(make_user, monkeypatch):
    _stub_delivery(monkeypatch)
    admin = make_user("11111111")
    farm_id = _farm(admin)
    inv = invitation_services.create_invitation(
        user=admin, farm_id=farm_id, email="x@example.com", role=FarmRole.OPERADOR
    )
    changed = invitation_services.change_invitation_role(
        user=admin, farm_id=farm_id, invitation_id=inv.pk, role=FarmRole.CONSULTA
    )
    changed.refresh_from_db()
    assert changed.role == FarmRole.CONSULTA
    assert _event(farm_id, EventType.INVITATION_ROLE_CHANGED) is not None


# --- Cancelación ---


def test_cancel_invitation_invalidates_token(make_user, monkeypatch):
    _stub_delivery(monkeypatch)
    admin = make_user("11111111")
    farm_id = _farm(admin)
    inv = invitation_services.create_invitation(
        user=admin, farm_id=farm_id, email="x@example.com", role=FarmRole.OPERADOR
    )
    cancelled = invitation_services.cancel_invitation(
        user=admin, farm_id=farm_id, invitation_id=inv.pk
    )
    assert cancelled.status == InvitationStatus.CANCELLED
    assert cancelled.pending_email is None
    assert cancelled.token_hash == ""
    assert _event(farm_id, EventType.INVITATION_CANCELLED) is not None


# --- Expiración perezosa ---


def test_lazy_expiry_releases_pending_email(make_user, monkeypatch):
    _stub_delivery(monkeypatch)
    admin = make_user("11111111")
    farm_id = _farm(admin)
    inv = invitation_services.create_invitation(
        user=admin, farm_id=farm_id, email="x@example.com", role=FarmRole.OPERADOR
    )
    FarmInvitation.objects.filter(pk=inv.pk).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    invitation_services.list_invitations(user=admin, farm_id=farm_id)
    inv.refresh_from_db()
    assert inv.status == InvitationStatus.EXPIRED
    assert inv.pending_email is None
    assert _event(farm_id, EventType.INVITATION_EXPIRED) is not None


# --- Entrega ---


def test_delivery_failure_records_audit(make_user, monkeypatch):
    _stub_delivery(monkeypatch, DeliveryOutcome.REJECTED)
    admin = make_user("11111111")
    farm_id = _farm(admin)

    with pytest.raises(AppError) as exc:
        invitation_services.create_invitation(
            user=admin, farm_id=farm_id, email="x@example.com", role=FarmRole.OPERADOR
        )

    inv = FarmInvitation.objects.get(farm_id=farm_id, email="x@example.com")
    assert exc.value.status_code == 502
    assert exc.value.extra["invitation_id"] == inv.pk
    assert inv.status == InvitationStatus.PENDING
    assert inv.delivery_pending is False
    assert inv.last_sent_at is None
    assert _event(farm_id, EventType.INVITATION_DELIVERY_FAILED) is not None


def _fail_only_for(monkeypatch, event_type):
    real = invitation_services.record_audit_event

    def fail_matching(*args, **kwargs):
        if kwargs.get("event_type") == event_type:
            raise RuntimeError("auditoría falló")
        return real(*args, **kwargs)

    monkeypatch.setattr(invitation_services, "record_audit_event", fail_matching)


def test_resend_audit_failure_rolls_back_delivery_flags(make_user, monkeypatch):
    admin = make_user("11111111")
    farm_id = _farm(admin)
    inv = _invite(farm_id, "x@example.com", admin=admin)
    _stub_delivery(monkeypatch, DeliveryOutcome.ACCEPTED)
    _fail_only_for(monkeypatch, EventType.INVITATION_RESENT)

    with pytest.raises(RuntimeError):
        invitation_services.resend_invitation(
            user=admin, farm_id=farm_id, invitation_id=inv.pk
        )

    inv.refresh_from_db()
    assert inv.delivery_pending is True
    assert inv.delivery_confirmed_at is None
    assert inv.last_sent_at is None


def test_delivery_failure_audit_failure_keeps_reservation(make_user, monkeypatch):
    admin = make_user("11111111")
    farm_id = _farm(admin)
    _stub_delivery(monkeypatch, DeliveryOutcome.REJECTED)
    _fail_only_for(monkeypatch, EventType.INVITATION_DELIVERY_FAILED)

    with pytest.raises(RuntimeError):
        invitation_services.create_invitation(
            user=admin, farm_id=farm_id, email="x@example.com", role=FarmRole.OPERADOR
        )

    inv = FarmInvitation.objects.get(farm_id=farm_id, email="x@example.com")
    assert inv.delivery_pending is True
    assert inv.delivery_confirmed_at is None


def test_late_accepted_delivery_does_not_record_resent(make_user, monkeypatch):
    admin = make_user("11111111")
    farm_id = _farm(admin)
    inv = _invite(farm_id, "x@example.com", admin=admin)
    nonce_a = uuid.uuid4()
    FarmInvitation.objects.filter(pk=inv.pk).update(
        delivery_pending=True,
        delivery_nonce=nonce_a,
        delivery_confirmed_at=None,
    )
    stale = FarmInvitation.objects.get(pk=inv.pk)
    nonce_b = uuid.uuid4()
    assert nonce_a is not None
    assert nonce_b is not None
    assert nonce_a != nonce_b
    FarmInvitation.objects.filter(pk=inv.pk).update(delivery_nonce=nonce_b)
    assert stale.delivery_nonce == nonce_a
    assert FarmInvitation.objects.get(pk=inv.pk).delivery_nonce == nonce_b
    _stub_delivery(monkeypatch, DeliveryOutcome.ACCEPTED)

    invitation_services._finalize_delivery(
        invitation=stale,
        farm=stale.farm,
        actor=admin,
        code="ABCDE-FGHJK",
        success_event_type=EventType.INVITATION_RESENT,
    )

    assert _event(farm_id, EventType.INVITATION_RESENT) is None
    record = FarmInvitation.objects.get(pk=inv.pk)
    assert record.delivery_nonce == nonce_b
    assert record.delivery_pending is True
    assert record.delivery_confirmed_at is None


def test_late_rejected_delivery_does_not_record_failure(make_user, monkeypatch):
    admin = make_user("11111111")
    farm_id = _farm(admin)
    inv = _invite(farm_id, "x@example.com", admin=admin)
    nonce_a = uuid.uuid4()
    FarmInvitation.objects.filter(pk=inv.pk).update(
        delivery_pending=True,
        delivery_nonce=nonce_a,
        delivery_confirmed_at=None,
    )
    stale = FarmInvitation.objects.get(pk=inv.pk)
    nonce_b = uuid.uuid4()
    assert nonce_a is not None
    assert nonce_b is not None
    assert nonce_a != nonce_b
    FarmInvitation.objects.filter(pk=inv.pk).update(delivery_nonce=nonce_b)
    assert stale.delivery_nonce == nonce_a
    assert FarmInvitation.objects.get(pk=inv.pk).delivery_nonce == nonce_b
    _stub_delivery(monkeypatch, DeliveryOutcome.REJECTED)

    invitation_services._finalize_delivery(
        invitation=stale,
        farm=stale.farm,
        actor=admin,
        code="ABCDE-FGHJK",
    )

    assert _event(farm_id, EventType.INVITATION_DELIVERY_FAILED) is None
    record = FarmInvitation.objects.get(pk=inv.pk)
    assert record.delivery_nonce == nonce_b
    assert record.delivery_pending is True
    assert record.delivery_confirmed_at is None


def test_indeterminate_delivery_preserves_reservation(make_user, monkeypatch):
    admin = make_user("11111111")
    farm_id = _farm(admin)
    inv = _invite(farm_id, "x@example.com", admin=admin)
    nonce = uuid.uuid4()
    now = timezone.now()
    FarmInvitation.objects.filter(pk=inv.pk).update(
        delivery_pending=True,
        delivery_reserved_at=now,
        delivery_nonce=nonce,
        delivery_confirmed_at=None,
    )
    _stub_delivery(monkeypatch, DeliveryOutcome.INDETERMINATE)
    current = FarmInvitation.objects.get(pk=inv.pk)

    invitation_services._finalize_delivery(
        invitation=current,
        farm=current.farm,
        actor=admin,
        code="ABCDE-FGHJK",
        success_event_type=EventType.INVITATION_RESENT,
    )

    record = FarmInvitation.objects.get(pk=inv.pk)
    assert record.delivery_pending is True
    assert record.delivery_reserved_at == now
    assert record.delivery_nonce == nonce
    assert record.delivery_confirmed_at is None
    assert _event(farm_id, EventType.INVITATION_RESENT) is None
    assert _event(farm_id, EventType.INVITATION_DELIVERY_FAILED) is None


# --- Aceptación ---


def test_accept_valid_creates_membership(make_user):
    admin = make_user("11111111")
    farm_id = _farm(admin)
    invited = make_user("22222222", correo="invited@example.com", email_verified=True)
    _invite(farm_id, "invited@example.com", token="tok-123", admin=admin)

    membership = invitation_services.accept_invitation(user=invited, token="tok-123")

    assert membership.farm_id == farm_id
    assert membership.role == FarmRole.OPERADOR
    assert membership.status == MembershipStatus.ACTIVE
    assert _event(farm_id, EventType.MEMBER_JOINED) is not None
    assert _event(farm_id, EventType.INVITATION_ACCEPTED) is not None
    inv = FarmInvitation.objects.get(farm_id=farm_id)
    assert inv.status == InvitationStatus.ACCEPTED
    assert inv.pending_email is None


def test_accept_reused_token_404(make_user):
    admin = make_user("11111111")
    farm_id = _farm(admin)
    invited = make_user("22222222", correo="invited@example.com", email_verified=True)
    _invite(farm_id, "invited@example.com", token="tok")
    invitation_services.accept_invitation(user=invited, token="tok")
    with pytest.raises(AppError) as exc:
        invitation_services.accept_invitation(user=invited, token="tok")
    assert exc.value.status_code == 404


def test_accept_unverified_email_404(make_user):
    admin = make_user("11111111")
    farm_id = _farm(admin)
    invited = make_user("22222222", correo="invited@example.com", email_verified=False)
    _invite(farm_id, "invited@example.com", token="tok")
    with pytest.raises(AppError) as exc:
        invitation_services.accept_invitation(user=invited, token="tok")
    assert exc.value.status_code == 404


def test_accept_ambiguous_identity_404(make_user):
    admin = make_user("11111111")
    farm_id = _farm(admin)
    invited_a = make_user("22222222", correo="dup@example.com", email_verified=True)
    make_user("33333333", correo="dup@example.com", email_verified=True)
    _invite(farm_id, "dup@example.com", token="tok")
    with pytest.raises(AppError) as exc:
        invitation_services.accept_invitation(user=invited_a, token="tok")
    assert exc.value.status_code == 404


def test_accept_active_membership_409(make_user):
    admin = make_user("11111111")
    farm_id = _farm(admin)
    invited = make_user("22222222", correo="invited@example.com", email_verified=True)
    FarmMembership.objects.create(farm_id=farm_id, user=invited, role=FarmRole.OPERADOR)
    _invite(farm_id, "invited@example.com", token="tok")
    with pytest.raises(AppError) as exc:
        invitation_services.accept_invitation(user=invited, token="tok")
    assert exc.value.status_code == 409


def test_accept_reactivates_same_membership(make_user):
    admin = make_user("11111111")
    farm_id = _farm(admin)
    invited = make_user("22222222", correo="invited@example.com", email_verified=True)
    membership = FarmMembership.objects.create(
        farm_id=farm_id, user=invited, role=FarmRole.OPERADOR
    )
    services.revoke_member(user=admin, farm_id=farm_id, membership_id=membership.pk)
    _invite(farm_id, "invited@example.com", role=FarmRole.CONSULTA, token="tok")

    result = invitation_services.accept_invitation(user=invited, token="tok")

    assert result.pk == membership.pk
    result.refresh_from_db()
    assert result.status == MembershipStatus.ACTIVE
    assert result.role == FarmRole.CONSULTA
    assert result.revoked_at is None
    assert result.left_at is None
    assert result.revoked_by_id is None
    assert _event(farm_id, EventType.MEMBER_REACTIVATED) is not None


def test_accept_inactive_farm_404(make_user):
    admin = make_user("11111111")
    farm_id = _farm(admin)
    invited = make_user("22222222", correo="invited@example.com", email_verified=True)
    _invite(farm_id, "invited@example.com", token="tok")
    services.deactivate_farm(user=admin, farm_id=farm_id)
    with pytest.raises(AppError) as exc:
        invitation_services.accept_invitation(user=invited, token="tok")
    assert exc.value.status_code == 404


# --- Cobertura adicional de FARMS-108 ---


def test_change_invitation_role_rotates_token_and_resends(make_user, monkeypatch):
    admin = make_user("11111111")
    farm_id = _farm(admin)
    invited = make_user("22222222", correo="x@example.com", email_verified=True)
    sent = []
    tokens = iter(["first-token", "second-token"])
    monkeypatch.setattr(
        "apps.farms.invitation_services._new_token", lambda: next(tokens)
    )
    # F10.3.3-A: el correo lleva el código, no el token.
    monkeypatch.setattr(
        "apps.farms.invitation_services._send_invitation",
        lambda *, email, code, farm_name, role, expires_at: (
            sent.append((code, role)) or DeliveryOutcome.ACCEPTED
        ),
    )
    invitation = invitation_services.create_invitation(
        user=admin, farm_id=farm_id, email="x@example.com", role=FarmRole.OPERADOR
    )
    old_hash = invitation.token_hash

    changed = invitation_services.change_invitation_role(
        user=admin, farm_id=farm_id, invitation_id=invitation.pk, role=FarmRole.CONSULTA
    )
    changed.refresh_from_db()

    assert changed.role == FarmRole.CONSULTA
    assert changed.status == InvitationStatus.PENDING
    assert changed.token_hash != old_hash
    assert changed.token_hash == _sha("second-token")
    assert [role for _code, role in sent] == [FarmRole.OPERADOR, FarmRole.CONSULTA]
    assert sent[0][0] != sent[1][0]

    with pytest.raises(AppError) as exc:
        invitation_services.accept_invitation(user=invited, token="first-token")
    assert exc.value.status_code == 404


def test_accept_no_verified_account_matching_404(make_user):
    admin = make_user("11111111")
    farm_id = _farm(admin)
    invited = make_user("22222222", correo="other@example.com", email_verified=True)
    _invite(farm_id, "ghost@example.com", token="tok")

    with pytest.raises(AppError) as exc:
        invitation_services.accept_invitation(user=invited, token="tok")

    assert exc.value.status_code == 404
    assert exc.value.message == "invitación no encontrada"
    assert not FarmMembership.objects.filter(farm_id=farm_id, user=invited).exists()
    invitation = FarmInvitation.objects.get(farm_id=farm_id)
    assert invitation.status == InvitationStatus.PENDING
    assert invitation.token_hash == _sha("tok")


def test_accept_reactivates_left_membership(make_user):
    admin = make_user("11111111")
    farm_id = _farm(admin)
    invited = make_user("22222222", correo="invited@example.com", email_verified=True)
    membership = FarmMembership.objects.create(
        farm_id=farm_id, user=invited, role=FarmRole.OPERADOR
    )
    services.leave_farm(user=invited, farm_id=farm_id)
    _invite(farm_id, "invited@example.com", role=FarmRole.CONSULTA, token="tok")

    result = invitation_services.accept_invitation(user=invited, token="tok")

    assert result.pk == membership.pk
    result.refresh_from_db()
    assert result.status == MembershipStatus.ACTIVE
    assert result.role == FarmRole.CONSULTA
    assert result.left_at is None
    assert result.revoked_at is None
    assert result.revoked_by_id is None
    assert FarmMembership.objects.filter(farm_id=farm_id, user=invited).count() == 1
    assert _event(farm_id, EventType.MEMBER_REACTIVATED) is not None


@pytest.mark.django_db(transaction=True)
def test_concurrent_accept_same_invitation_single_membership(make_user):
    admin = make_user("11111111")
    farm_id = _farm(admin)
    invited = make_user("22222222", correo="invited@example.com", email_verified=True)
    token = "tok-123"
    _invite(farm_id, "invited@example.com", role=FarmRole.OPERADOR, token=token, admin=admin)

    barrier = Barrier(2)

    def accept():
        try:
            barrier.wait(timeout=5)
            return invitation_services.accept_invitation(user=invited, token=token)
        except AppError as exc:
            return exc
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(accept), pool.submit(accept)]
        results = [future.result(timeout=10) for future in futures]

    successes = [result for result in results if isinstance(result, FarmMembership)]
    errors = [result for result in results if isinstance(result, AppError)]
    assert len(successes) == 1
    assert len(errors) == 1
    assert errors[0].status_code == 404

    memberships = FarmMembership.objects.filter(farm_id=farm_id, user=invited)
    assert memberships.count() == 1
    membership = memberships.get()
    assert membership.status == MembershipStatus.ACTIVE
    assert membership.role == FarmRole.OPERADOR

    invitation = FarmInvitation.objects.get(farm_id=farm_id)
    assert invitation.status == InvitationStatus.ACCEPTED
    assert invitation.token_hash == ""
    assert invitation.pending_email is None

    assert (
        AuditEvent.objects.filter(
            farm_id=farm_id, event_type=EventType.INVITATION_ACCEPTED
        ).count()
        == 1
    )
    assert (
        AuditEvent.objects.filter(
            farm_id=farm_id, event_type=EventType.MEMBER_JOINED
        ).count()
        == 1
    )


@pytest.mark.django_db(transaction=True)
def test_concurrent_create_duplicate_invitation_single_pending(make_user, monkeypatch):
    _stub_delivery(monkeypatch)
    admin = make_user("11111111")
    farm_id = _farm(admin)

    barrier = Barrier(2)

    def create():
        try:
            barrier.wait(timeout=5)
            return invitation_services.create_invitation(
                user=admin,
                farm_id=farm_id,
                email="x@example.com",
                role=FarmRole.OPERADOR,
            )
        except AppError as exc:
            return exc
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(create), pool.submit(create)]
        results = [future.result(timeout=10) for future in futures]

    invitations = [result for result in results if isinstance(result, FarmInvitation)]
    errors = [result for result in results if isinstance(result, AppError)]
    assert len(invitations) == 1
    assert len(errors) == 1
    assert errors[0].status_code == 409

    pending = FarmInvitation.objects.filter(farm_id=farm_id, pending_email="x@example.com")
    assert pending.count() == 1
    invitation = pending.get()
    assert invitation.status == InvitationStatus.PENDING
    assert invitation.token_hash != ""

    assert (
        AuditEvent.objects.filter(
            farm_id=farm_id, event_type=EventType.INVITATION_CREATED
        ).count()
        == 1
    )
