"""Prueba cifrado, OTP y proveedores externos aislados.

@author Cristian Deysdayr Jimenez
"""
from datetime import timedelta
from types import SimpleNamespace

import pytest
import requests
from django.test import override_settings
from django.utils import timezone

from apps.accounts.errors import AppError
from apps.accounts.integrations import email, sms, whatsapp
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
    captured = {}

    def post(*args, **kwargs):
        captured["json"] = kwargs["json"]
        return SimpleNamespace(status_code=201)

    monkeypatch.setattr("apps.accounts.integrations.brevo_client.requests.post", post)
    email.send_code("user@example.com", "123456", 5)
    assert captured["json"]["to"] == [{"email": "user@example.com"}]
    assert "htmlContent" in captured["json"]
    assert "123456" in captured["json"]["htmlContent"]
    assert "Caduca en 5 minutos" in captured["json"]["htmlContent"]
    assert "no compartas" in captured["json"]["htmlContent"].lower()


def test_brevo_sms_and_whatsapp(monkeypatch):
    """Brevo recibe el SMS de texto y la plantilla de WhatsApp."""
    monkeypatch.setenv("BREVO_API_KEY", "test-key")
    monkeypatch.setenv("BREVO_SMS_SENDER", "AGROTECH")
    monkeypatch.setenv("BREVO_WHATSAPP_SENDER", "573001112233")
    monkeypatch.setenv("BREVO_WHATSAPP_TEMPLATE_ID", "42")
    sent = []

    def post(*args, **kwargs):
        sent.append(kwargs["json"])
        return SimpleNamespace(status_code=201)

    monkeypatch.setattr("apps.accounts.integrations.brevo_client.requests.post", post)
    sms.send_code("3001234567", "123456")
    whatsapp.send_code("3001234567", "123456")
    assert sent[0]["recipient"] == "573001234567"
    assert sent[0]["content"] == "Código AGROTECH-T: 123456. Caduca en pocos minutos."
    assert sent[1]["contactNumbers"] == ["573001234567"]
    assert sent[1]["templateId"] == 42
    assert sent[1]["params"] == {"1": "123456"}


def test_brevo_failure_and_missing_setup(monkeypatch):
    """Un rechazo de Brevo o una plantilla vacía no entregan el código."""
    monkeypatch.setenv("BREVO_API_KEY", "test-key")
    monkeypatch.setenv("BREVO_SMS_SENDER", "")
    with pytest.raises(AppError):
        sms.send_code("3001234567", "123456")
    monkeypatch.setenv("BREVO_SMS_SENDER", "AGROTECH")
    monkeypatch.setattr(
        "apps.accounts.integrations.brevo_client.requests.post",
        lambda *args, **kwargs: SimpleNamespace(status_code=400),
    )
    with pytest.raises(AppError):
        sms.send_code("3001234567", "123456")

    def boom(*args, **kwargs):
        raise requests.ConnectionError("caido")

    monkeypatch.setattr("apps.accounts.integrations.brevo_client.requests.post", boom)
    with pytest.raises(AppError):
        sms.send_code("3001234567", "123456")
    monkeypatch.delenv("BREVO_WHATSAPP_TEMPLATE_ID", raising=False)
    with pytest.raises(AppError):
        whatsapp.send_code("3001234567", "123456")


def test_phone_channels_require_brevo(monkeypatch):
    """Sin clave de Brevo no sale el mensaje de texto."""
    monkeypatch.delenv("BREVO_API_KEY", raising=False)
    monkeypatch.setenv("BREVO_SMS_SENDER", "AGROTECH")
    with pytest.raises(AppError):
        sms.send_code("3001234567", "123456")


@pytest.mark.django_db
@pytest.mark.parametrize("channel", ["whatsapp", "sms"])
def test_phone_code_is_checked_locally(user, monkeypatch, channel):
    """El código del celular se valida aquí, no en el proveedor."""
    monkeypatch.setattr("apps.accounts.services.otp_service.secrets.randbelow", lambda _: 123456)
    monkeypatch.setattr(
        f"apps.accounts.services.otp_service.{channel}.send_code",
        lambda *args: None,
    )
    otp_service.request_code(user, channel)
    assert LoginCode.objects.get(user=user).code_hash != "twilio-verify"
    otp_service.verify_code(user, "123456")
    assert not LoginCode.objects.filter(user=user).exists()
