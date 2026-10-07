"""Prueba los contratos HTTP oficiales y heredados.

@author Cristian Deysdayr Jimenez
"""
import pytest
from django.contrib.auth.hashers import check_password
from rest_framework.test import APIClient

from apps.accounts.models import Session, User
from apps.accounts.services import token_service
from apps.accounts.tests.constants import (
    CREDENTIAL_FIELD,
    NEW_CREDENTIAL,
    REGISTER_CREDENTIAL,
    VALID_CREDENTIAL,
    WEAK_CREDENTIAL,
    WRONG_CREDENTIAL,
)

REGISTER = {
    "first_name": "Luisa",
    "last_name": "Gómez",
    "identification": "98765432",
    "phone": "3012223344",
    "correo": "luisa@example.com",
    CREDENTIAL_FIELD: REGISTER_CREDENTIAL,
}


@pytest.fixture
def client() -> APIClient:
    """Retorna un cliente HTTP de DRF."""
    return APIClient()


@pytest.mark.django_db
def test_register_creates_hashed_account(client):
    """El registro almacena un hash y responde 201."""
    response = client.post("/api/v1/accounts/", REGISTER, format="json")
    account = User.objects.get(identification="98765432")
    assert response.status_code == 201
    assert check_password(REGISTER_CREDENTIAL, account.password_hash)


@pytest.mark.django_db
def test_register_rejects_duplicate_and_weak_password(client):
    """El registro informa duplicados y contraseñas débiles."""
    assert client.post("/api/v1/registro", REGISTER, format="json").status_code == 201
    duplicate = client.post("/api/v1/registro", REGISTER, format="json")
    weak = client.post(
        "/api/v1/accounts/",
        {**REGISTER, "identification": "12345", CREDENTIAL_FIELD: WEAK_CREDENTIAL},
        format="json",
    )
    assert duplicate.status_code == 409
    assert weak.status_code == 400


@pytest.mark.django_db
def test_login_renew_me_and_logout(client, user):
    """La sesión permite consultar, rotar y revocar el acceso."""
    login = client.post(
        "/api/v1/auth/login/",
        {"identification": user.identification, CREDENTIAL_FIELD: VALID_CREDENTIAL},
        format="json",
    )
    token = login.data["token"]
    renewed = client.post(
        "/api/v1/auth/session/renew/",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )
    assert login.status_code == 200
    assert renewed.status_code == 200
    assert renewed.data["token"] != token
    session = Session.objects.get(user=user)
    assert session.token_hash == token_service.digest(renewed.data["token"])
    assert client.get("/api/v1/auth/me/", HTTP_AUTHORIZATION=f"Bearer {token}").status_code == 401
    current = f"Bearer {renewed.data['token']}"
    profile = client.get("/api/v1/auth/me/", HTTP_AUTHORIZATION=current)
    logout = client.post("/api/v1/auth/logout/", HTTP_AUTHORIZATION=current)
    rejected = client.get("/api/v1/auth/me/", HTTP_AUTHORIZATION=current)
    assert profile.status_code == 200
    assert profile.data["identification"] == user.identification
    assert logout.status_code == 200
    assert rejected.status_code == 401


@pytest.mark.django_db
def test_login_rejects_invalid_credentials(client, user):
    """El acceso responde un error genérico para credenciales inválidas."""
    response = client.post(
        "/api/v1/auth/login/",
        {"identification": user.identification, CREDENTIAL_FIELD: WRONG_CREDENTIAL},
        format="json",
    )
    assert response.status_code == 401
    assert response.data == {"error": "credenciales invalidas"}


@pytest.mark.django_db
def test_login_temporarily_blocks_repeated_failures(client, user):
    """Cinco contraseñas incorrectas bloquean temporalmente la cuenta."""
    payload = {"identification": user.identification, CREDENTIAL_FIELD: WRONG_CREDENTIAL}
    for _ in range(5):
        assert client.post("/api/v1/auth/login/", payload, format="json").status_code == 401
    blocked = client.post(
        "/api/v1/auth/login/",
        {"identification": user.identification, CREDENTIAL_FIELD: VALID_CREDENTIAL},
        format="json",
    )
    user.refresh_from_db()
    assert blocked.status_code in {401, 429}
    assert user.locked_until is not None


@pytest.mark.django_db
def test_email_recovery_resets_password(client, user, monkeypatch):
    """Un OTP válido cambia la contraseña."""
    monkeypatch.setattr("apps.accounts.services.otp_service.secrets.randbelow", lambda _: 123456)
    monkeypatch.setattr("apps.accounts.integrations.email.send_code", lambda *_: None)
    request = {"identification": user.identification, "method": "correo"}
    sent = client.post("/api/v1/auth/password/recovery/", request, format="json")
    repeated = client.post("/api/v1/auth/password/recovery/", request, format="json")
    reset = client.post(
        "/api/v1/auth/password/reset/",
        {
            "identification": user.identification,
            "code": "123456",
            CREDENTIAL_FIELD: NEW_CREDENTIAL,
        },
        format="json",
    )
    user.refresh_from_db()
    assert sent.status_code == 200
    assert repeated.status_code == 429
    assert reset.status_code == 200
    assert check_password(NEW_CREDENTIAL, user.password_hash)


@pytest.mark.django_db
def test_recovery_rejects_unknown_account(client):
    """La recuperación no revela si la cuenta existe."""
    response = client.post(
        "/api/v1/auth/password/recovery/",
        {"identification": "55555555", "method": "correo"},
        format="json",
    )
    assert response.status_code == 400
    assert response.data == {"error": "no se pudo enviar el codigo"}
