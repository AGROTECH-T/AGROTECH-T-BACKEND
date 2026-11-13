"""Aislamiento entre propósitos de OTP (recovery y verification)."""
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts.errors import AppError
from apps.accounts.models import LoginCode, OTPPurpose
from apps.accounts.services import otp_service

pytestmark = pytest.mark.django_db


def _stub_email(monkeypatch):
    monkeypatch.setattr("apps.accounts.integrations.email.send_code", lambda *args: None)


def test_verification_code_is_validated(user, monkeypatch):
    monkeypatch.setattr("apps.accounts.services.otp_service.secrets.randbelow", lambda _: 123456)
    _stub_email(monkeypatch)
    otp_service.request_code(user, "correo", OTPPurpose.VERIFICATION)
    otp_service.verify_code(user, "123456", OTPPurpose.VERIFICATION)
    assert not LoginCode.objects.filter(user=user, purpose=OTPPurpose.VERIFICATION).exists()


def test_verification_code_rejects_wrong(user, monkeypatch):
    monkeypatch.setattr("apps.accounts.services.otp_service.secrets.randbelow", lambda _: 123456)
    _stub_email(monkeypatch)
    otp_service.request_code(user, "correo", OTPPurpose.VERIFICATION)
    with pytest.raises(AppError):
        otp_service.verify_code(user, "000000", OTPPurpose.VERIFICATION)


def test_verification_code_rejects_expired(user, monkeypatch):
    monkeypatch.setattr("apps.accounts.services.otp_service.secrets.randbelow", lambda _: 123456)
    _stub_email(monkeypatch)
    otp_service.request_code(user, "correo", OTPPurpose.VERIFICATION)
    LoginCode.objects.filter(user=user, purpose=OTPPurpose.VERIFICATION).update(
        expires_at=timezone.now() - timedelta(seconds=1)
    )
    with pytest.raises(AppError):
        otp_service.verify_code(user, "123456", OTPPurpose.VERIFICATION)


def test_new_verification_invalidates_previous(user, monkeypatch):
    codes = iter([111111, 222222])
    monkeypatch.setattr(
        "apps.accounts.services.otp_service.secrets.randbelow",
        lambda _: next(codes),
    )
    _stub_email(monkeypatch)
    otp_service.request_code(user, "correo", OTPPurpose.VERIFICATION)
    LoginCode.objects.filter(user=user, purpose=OTPPurpose.VERIFICATION).update(
        sent_at=timezone.now() - timedelta(seconds=11)
    )
    otp_service.request_code(user, "correo", OTPPurpose.VERIFICATION)
    with pytest.raises(AppError):
        otp_service.verify_code(user, "111111", OTPPurpose.VERIFICATION)
    otp_service.verify_code(user, "222222", OTPPurpose.VERIFICATION)


def test_recovery_and_verification_coexist(user, monkeypatch):
    monkeypatch.setattr("apps.accounts.services.otp_service.secrets.randbelow", lambda _: 123456)
    _stub_email(monkeypatch)
    otp_service.request_code(user, "correo", OTPPurpose.RECOVERY)
    otp_service.request_code(user, "correo", OTPPurpose.VERIFICATION)
    assert LoginCode.objects.filter(user=user).count() == 2


def test_recovery_request_does_not_invalidate_verification(user, monkeypatch):
    monkeypatch.setattr("apps.accounts.services.otp_service.secrets.randbelow", lambda _: 123456)
    _stub_email(monkeypatch)
    otp_service.request_code(user, "correo", OTPPurpose.VERIFICATION)
    otp_service.request_code(user, "correo", OTPPurpose.RECOVERY)
    otp_service.verify_code(user, "123456", OTPPurpose.VERIFICATION)
    assert LoginCode.objects.filter(user=user, purpose=OTPPurpose.RECOVERY).exists()


def test_verification_request_does_not_invalidate_recovery(user, monkeypatch):
    monkeypatch.setattr("apps.accounts.services.otp_service.secrets.randbelow", lambda _: 123456)
    _stub_email(monkeypatch)
    otp_service.request_code(user, "correo", OTPPurpose.RECOVERY)
    otp_service.request_code(user, "correo", OTPPurpose.VERIFICATION)
    otp_service.verify_code(user, "123456", OTPPurpose.RECOVERY)
    assert LoginCode.objects.filter(user=user, purpose=OTPPurpose.VERIFICATION).exists()


def test_recovery_code_not_valid_as_verification(user, monkeypatch):
    monkeypatch.setattr("apps.accounts.services.otp_service.secrets.randbelow", lambda _: 123456)
    _stub_email(monkeypatch)
    otp_service.request_code(user, "correo", OTPPurpose.RECOVERY)
    with pytest.raises(AppError):
        otp_service.verify_code(user, "123456", OTPPurpose.VERIFICATION)


def test_verification_code_not_valid_as_recovery(user, monkeypatch):
    monkeypatch.setattr("apps.accounts.services.otp_service.secrets.randbelow", lambda _: 123456)
    _stub_email(monkeypatch)
    otp_service.request_code(user, "correo", OTPPurpose.VERIFICATION)
    with pytest.raises(AppError):
        otp_service.verify_code(user, "123456", OTPPurpose.RECOVERY)


def test_attempts_are_independent(user, monkeypatch):
    monkeypatch.setattr("apps.accounts.services.otp_service.secrets.randbelow", lambda _: 123456)
    _stub_email(monkeypatch)
    otp_service.request_code(user, "correo", OTPPurpose.RECOVERY)
    otp_service.request_code(user, "correo", OTPPurpose.VERIFICATION)
    with pytest.raises(AppError):
        otp_service.verify_code(user, "000000", OTPPurpose.RECOVERY)
    recovery = LoginCode.objects.get(user=user, purpose=OTPPurpose.RECOVERY)
    verification = LoginCode.objects.get(user=user, purpose=OTPPurpose.VERIFICATION)
    assert recovery.attempts == 1
    assert verification.attempts == 0
