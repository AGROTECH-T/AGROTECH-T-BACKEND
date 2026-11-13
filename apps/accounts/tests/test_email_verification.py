"""Pruebas de verificación y cambio de correo electrónico."""
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts.errors import AppError
from apps.accounts.models import LoginCode, OTPPurpose
from apps.accounts.services import auth_service, otp_service
from apps.trazabilidad.models import AuditEvent, EventType

pytestmark = pytest.mark.django_db


def _request_code(user, monkeypatch, purpose, digits=123456):
    monkeypatch.setattr(
        "apps.accounts.services.otp_service.secrets.randbelow",
        lambda _: digits,
    )
    monkeypatch.setattr("apps.accounts.integrations.email.send_code", lambda *args: None)
    otp_service.request_code(user, "correo", purpose)


# --- Verificación ---


def test_verify_email_success(user, monkeypatch):
    _request_code(user, monkeypatch, OTPPurpose.VERIFICATION)
    updated = auth_service.verify_email(user, "123456")
    assert updated.email_verified is True
    assert updated.email_verified_at is not None
    assert AuditEvent.objects.filter(
        event_type=EventType.EMAIL_VERIFIED,
        entity_id=str(user.pk),
    ).exists()


def test_verify_email_rejects_wrong_code(user, monkeypatch):
    _request_code(user, monkeypatch, OTPPurpose.VERIFICATION)
    with pytest.raises(AppError):
        auth_service.verify_email(user, "000000")
    user.refresh_from_db()
    assert user.email_verified is False


def test_verify_email_rejects_expired_code(user, monkeypatch):
    _request_code(user, monkeypatch, OTPPurpose.VERIFICATION)
    LoginCode.objects.filter(user=user, purpose=OTPPurpose.VERIFICATION).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    with pytest.raises(AppError):
        auth_service.verify_email(user, "123456")
    user.refresh_from_db()
    assert user.email_verified is False


def test_verify_email_rejects_recovery_code(user, monkeypatch):
    _request_code(user, monkeypatch, OTPPurpose.RECOVERY)
    with pytest.raises(AppError):
        auth_service.verify_email(user, "123456")
    user.refresh_from_db()
    assert user.email_verified is False


def test_verify_email_already_verified_is_noop(user, monkeypatch):
    _request_code(user, monkeypatch, OTPPurpose.VERIFICATION)
    auth_service.verify_email(user, "123456")
    assert (
        AuditEvent.objects.filter(
            event_type=EventType.EMAIL_VERIFIED,
            entity_id=str(user.pk),
        ).count()
        == 1
    )
    auth_service.verify_email(user, "111111")
    assert (
        AuditEvent.objects.filter(
            event_type=EventType.EMAIL_VERIFIED,
            entity_id=str(user.pk),
        ).count()
        == 1
    )


# --- Cambio de correo ---


def test_update_profile_changes_email_and_resets_verification(user, monkeypatch):
    _request_code(user, monkeypatch, OTPPurpose.VERIFICATION)
    auth_service.verify_email(user, "123456")
    user.refresh_from_db()
    assert user.email_verified is True

    auth_service.update_profile(user, {"correo": "nuevo@example.com"})
    user.refresh_from_db()
    assert user.correo == "nuevo@example.com"
    assert user.email_verified is False
    assert user.email_verified_at is None
    assert AuditEvent.objects.filter(
        event_type=EventType.EMAIL_CHANGED,
        entity_id=str(user.pk),
    ).exists()


def test_update_profile_normalizes_email(user):
    auth_service.update_profile(user, {"correo": "  NUEVO@Example.COM "})
    user.refresh_from_db()
    assert user.correo == "nuevo@example.com"
    assert user.email_verified is False


def test_email_change_invalidates_verification_keeps_recovery(user, monkeypatch):
    _request_code(user, monkeypatch, OTPPurpose.VERIFICATION)
    _request_code(user, monkeypatch, OTPPurpose.RECOVERY)

    auth_service.update_profile(user, {"correo": "otro@example.com"})

    assert not LoginCode.objects.filter(
        user=user, purpose=OTPPurpose.VERIFICATION
    ).exists()
    assert LoginCode.objects.filter(
        user=user, purpose=OTPPurpose.RECOVERY
    ).exists()


def test_update_profile_same_email_keeps_verification(user, monkeypatch):
    _request_code(user, monkeypatch, OTPPurpose.VERIFICATION)
    auth_service.verify_email(user, "123456")
    user.refresh_from_db()
    assert user.email_verified is True

    auth_service.update_profile(user, {"correo": "ANA@example.com"})
    user.refresh_from_db()
    assert user.correo == "ana@example.com"
    assert user.email_verified is True


# --- Consistencia ---


def test_verify_email_rolls_back_on_audit_failure(user, monkeypatch):
    _request_code(user, monkeypatch, OTPPurpose.VERIFICATION)

    def fail(*args, **kwargs):
        raise RuntimeError("auditoría falló")

    monkeypatch.setattr(auth_service, "record_audit_event", fail)
    with pytest.raises(RuntimeError):
        auth_service.verify_email(user, "123456")
    user.refresh_from_db()
    assert user.email_verified is False
