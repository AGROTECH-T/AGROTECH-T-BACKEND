"""Prueba cifrado, OTP y proveedores externos aislados.

@author Cristian Deysdayr Jimenez
"""
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.test import override_settings
from django.utils import timezone

from apps.accounts.errors import AppError
from apps.accounts.integrations import email, whatsapp
from apps.accounts.models import LoginCode
from apps.accounts.services import otp_service, token_service


def test_token_round_trip_and_invalid_token():
    """El JWE conserva el id y rechaza contenido manipulado."""
    token = token_service.issue(42)
    claims = token_service.read(token)
    assert claims.user_id == 42
    assert claims.token_id
    assert token_service.digest(token) not in token
    with pytest.raises(AppError):
        token_service.read(f"{token}x")


def test_invalid_token_configuration(monkeypatch):
    """Una clave que no tiene 32 bytes se rechaza."""
    monkeypatch.setenv("JWT_ENCRYPTION_KEY", "aW52YWxpZA==")
    with pytest.raises(AppError):
        token_service.issue(1)


@pytest.mark.django_db
def test_wrong_and_expired_email_codes(user):
    """Los OTP erróneos suman intentos y los vencidos se eliminan."""
    LoginCode.objects.create(
        user=user,
        code_hash="wrong",
        expires_at=timezone.now() + timedelta(minutes=5),
        channel="correo",
    )
    with pytest.raises(AppError):
        otp_service.verify_code(user, "000000")
    assert LoginCode.objects.get(user=user).attempts == 1
    LoginCode.objects.filter(user=user).update(expires_at=timezone.now() - timedelta(seconds=1))
    with pytest.raises(AppError):
        otp_service.verify_code(user, "000000")
    assert not LoginCode.objects.filter(user=user).exists()


@override_settings(DEFAULT_FROM_EMAIL="sender@example.com")
def test_smtp_and_brevo_delivery(monkeypatch):
    """Correo usa SMTP o Brevo según la configuración."""
    monkeypatch.delenv("BREVO_API_KEY", raising=False)
    monkeypatch.setattr("apps.accounts.integrations.email.send_mail", lambda *args, **kwargs: 1)
    email.send_code("user@example.com", "123456")
    monkeypatch.setenv("BREVO_API_KEY", "test-key")
    monkeypatch.setattr(
        "apps.accounts.integrations.email.requests.post",
        lambda *args, **kwargs: SimpleNamespace(status_code=201),
    )
    email.send_code("user@example.com", "123456")


def test_twilio_whatsapp_send_and_check(monkeypatch):
    """Twilio envía y valida códigos de WhatsApp mediante peticiones simuladas."""
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC-test")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "token")
    monkeypatch.setenv("TWILIO_VERIFY_SERVICE_SID", "VA-test")
    requests = []
    approved = SimpleNamespace(status_code=200, json=lambda: {"status": "approved"})

    def request(*args, **kwargs):
        requests.append(kwargs["data"])
        return approved

    monkeypatch.setattr(
        "apps.accounts.integrations.whatsapp.requests.post",
        request,
    )
    whatsapp.send_code("3001234567")
    assert whatsapp.check_code("3001234567", "123456")
    assert requests[0] == {"To": "+573001234567", "Channel": "whatsapp"}


def test_twilio_requires_configuration(monkeypatch):
    """Twilio rechaza llamadas sin credenciales."""
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    with pytest.raises(AppError):
        whatsapp.send_code("3001234567")
