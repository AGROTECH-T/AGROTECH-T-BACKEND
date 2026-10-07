"""Reenvío del código y aviso de IP de Brevo.

Propósito: el código nuevo reemplaza al anterior y la IP bloqueada se entiende.
Contexto: recuperación por correo, WhatsApp o mensaje de texto.
@author Cristian Deysdayr Jimenez
"""
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.errors import AppError
from apps.accounts.integrations import sms
from apps.accounts.models import LoginCode
from apps.accounts.services import otp_service


@pytest.mark.django_db
def test_resend_waits_ten_seconds(user, monkeypatch):
    """Un segundo envío inmediato pide esperar."""
    monkeypatch.setattr("apps.accounts.integrations.email.send_code", lambda *args: None)
    otp_service.request_code(user, "correo")
    with pytest.raises(AppError) as error:
        otp_service.request_code(user, "correo")
    assert error.value.status_code == 429


@pytest.mark.django_db
def test_new_code_replaces_the_previous_one(user, monkeypatch):
    """A los diez segundos el código anterior deja de servir."""
    codes = iter([111111, 222222])
    monkeypatch.setattr(
        "apps.accounts.services.otp_service.secrets.randbelow",
        lambda _: next(codes),
    )
    monkeypatch.setattr("apps.accounts.integrations.email.send_code", lambda *args: None)
    otp_service.request_code(user, "correo")
    LoginCode.objects.filter(user=user).update(sent_at=timezone.now() - timedelta(seconds=11))
    otp_service.request_code(user, "correo")
    with pytest.raises(AppError):
        otp_service.verify_code(user, "111111")
    otp_service.verify_code(user, "222222")
    assert not LoginCode.objects.filter(user=user).exists()


@pytest.mark.django_db
def test_check_keeps_the_code_until_reset(user, monkeypatch):
    """La comprobación no gasta el código. El cambio de clave sí."""
    monkeypatch.setattr("apps.accounts.services.otp_service.secrets.randbelow", lambda _: 123456)
    monkeypatch.setattr("apps.accounts.integrations.email.send_code", lambda *args: None)
    otp_service.request_code(user, "correo")
    otp_service.check_code(user, "123456")
    assert LoginCode.objects.filter(user=user).exists()
    otp_service.verify_code(user, "123456")
    assert not LoginCode.objects.filter(user=user).exists()


@pytest.mark.django_db
def test_confirm_endpoint_keeps_the_code(user, monkeypatch):
    """La ruta de comprobación responde éxito y no borra el código."""
    monkeypatch.setattr("apps.accounts.services.otp_service.secrets.randbelow", lambda _: 123456)
    monkeypatch.setattr("apps.accounts.integrations.email.send_code", lambda *args: None)
    otp_service.request_code(user, "correo")
    response = APIClient().post(
        "/api/v1/auth/password/confirm/",
        {"identification": user.identification, "code": "123456"},
        format="json",
    )
    assert response.status_code == 200
    assert LoginCode.objects.filter(user=user).exists()


def test_brevo_explains_an_unknown_ip(monkeypatch):
    """Una IP no autorizada se dice en el aviso."""
    monkeypatch.setenv("BREVO_API_KEY", "test-key")
    monkeypatch.setenv("BREVO_SMS_SENDER", "AGROTECH")

    def post(*args, **kwargs):
        return SimpleNamespace(
            status_code=401,
            json=lambda: {"message": "unrecognised IP address 152.200.171.202"},
        )

    monkeypatch.setattr("apps.accounts.integrations.brevo_client.requests.post", post)
    with pytest.raises(AppError) as error:
        sms.send_code("3001234567", "123456")
    assert error.value.message == "No se pudo enviar. En Brevo autoriza la IP 152.200.171.202."
