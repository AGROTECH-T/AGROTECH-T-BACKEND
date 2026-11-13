# ruff: noqa: S101
"""Pruebas del flujo de verificación OTP con reserva de entrega y nonce."""
import uuid

import pytest

from apps.accounts.errors import AppError
from apps.accounts.integrations.delivery import DeliveryOutcome
from apps.accounts.models import LoginCode, OTPPurpose, User
from apps.accounts.services import auth_service, otp_service

pytestmark = pytest.mark.django_db


def _stub_delivery(monkeypatch, outcome=DeliveryOutcome.ACCEPTED):
    monkeypatch.setattr(
        "apps.accounts.services.otp_service._deliver_verification",
        lambda *args: outcome,
    )


def _verification_record(user):
    return LoginCode.objects.get(user=user, purpose=OTPPurpose.VERIFICATION)


def test_request_reserves_delivery(user, monkeypatch):
    _stub_delivery(monkeypatch, DeliveryOutcome.INDETERMINATE)
    otp_service.request_verification_code(user)
    record = _verification_record(user)
    assert record.delivery_pending is True
    assert record.delivery_nonce is not None
    assert record.delivery_reserved_at is not None
    assert record.code_hash


def test_request_confirms_on_accepted(user, monkeypatch):
    _stub_delivery(monkeypatch, DeliveryOutcome.ACCEPTED)
    otp_service.request_verification_code(user)
    record = _verification_record(user)
    assert record.delivery_pending is False
    assert record.delivery_confirmed_at is not None


def test_request_releases_on_rejected(user, monkeypatch):
    _stub_delivery(monkeypatch, DeliveryOutcome.REJECTED)
    otp_service.request_verification_code(user)
    record = _verification_record(user)
    assert record.delivery_pending is False
    assert record.delivery_confirmed_at is None


def test_request_keeps_reservation_on_indeterminate(user, monkeypatch):
    _stub_delivery(monkeypatch, DeliveryOutcome.INDETERMINATE)
    otp_service.request_verification_code(user)
    record = _verification_record(user)
    assert record.delivery_pending is True


def test_confirm_works_while_delivery_pending(user, monkeypatch):
    _stub_delivery(monkeypatch, DeliveryOutcome.INDETERMINATE)
    monkeypatch.setattr(
        "apps.accounts.services.otp_service.secrets.randbelow", lambda _: 123456
    )
    otp_service.request_verification_code(user)
    updated = auth_service.verify_email(user, "123456")
    assert updated.email_verified is True


def test_late_delivery_does_not_touch_new_reservation(user, monkeypatch):
    _stub_delivery(monkeypatch, DeliveryOutcome.INDETERMINATE)
    otp_service.request_verification_code(user)
    old_nonce = _verification_record(user).delivery_nonce
    new_nonce = uuid.uuid4()
    LoginCode.objects.filter(user=user, purpose=OTPPurpose.VERIFICATION).update(
        delivery_pending=True, delivery_nonce=new_nonce, delivery_confirmed_at=None
    )
    otp_service._apply_verification_outcome(user, old_nonce, DeliveryOutcome.ACCEPTED)
    record = _verification_record(user)
    assert record.delivery_nonce == new_nonce
    assert record.delivery_pending is True
    assert record.delivery_confirmed_at is None


def test_request_uses_locked_user_email_when_object_is_stale(user, monkeypatch):
    stale_email = user.correo
    current_email = "actualizada@example.com"
    User.objects.filter(pk=user.pk).update(correo=current_email)
    assert user.correo == stale_email

    captured = {}

    def capture(address, digits, minutes):
        captured["address"] = address
        return DeliveryOutcome.ACCEPTED

    monkeypatch.setattr(
        "apps.accounts.services.otp_service._deliver_verification", capture
    )

    otp_service.request_verification_code(user)

    assert captured["address"] == current_email
    record = _verification_record(user)
    assert record.user_id == user.pk


def test_request_cooldown(user, monkeypatch):
    _stub_delivery(monkeypatch)
    otp_service.request_verification_code(user)
    with pytest.raises(AppError) as exc:
        otp_service.request_verification_code(user)
    assert exc.value.status_code == 429


def test_recovery_flow_unchanged(user, monkeypatch):
    monkeypatch.setattr("apps.accounts.integrations.email.send_code", lambda *args: None)
    monkeypatch.setattr(
        "apps.accounts.services.otp_service.secrets.randbelow", lambda _: 123456
    )
    otp_service.request_code(user, "correo", OTPPurpose.RECOVERY)
    record = LoginCode.objects.get(user=user, purpose=OTPPurpose.RECOVERY)
    assert record.delivery_pending is False
    assert record.delivery_nonce is None
