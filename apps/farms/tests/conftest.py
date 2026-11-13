# ruff: noqa: S101, S105
"""Fixtures compartidas para las pruebas de fincas.

Propósito: crear cuentas reales y autenticar con el endpoint existente.
Contexto: la API usa JWE y APIClient, igual que las pruebas de cuentas.
"""
import base64

import pytest
from django.contrib.auth.hashers import make_password
from django.core.cache import cache
from rest_framework.test import APIClient

from apps.accounts.models import User

VALID_PASSWORD = "Secure1!"


@pytest.fixture(autouse=True)
def token_key(monkeypatch):
    """Configura claves deterministas exclusivamente para pruebas."""
    cache.clear()
    monkeypatch.setenv("JWT_ENCRYPTION_KEY", base64.b64encode(b"k" * 32).decode())
    monkeypatch.setenv("OTP_HMAC_KEY", "test-otp-key")
    yield
    cache.clear()


@pytest.fixture
def client() -> APIClient:
    """Retorna un cliente HTTP de DRF sin credenciales."""
    return APIClient()


@pytest.fixture
def make_user(db):
    """Retorna una fábrica de cuentas válidas con documento único."""

    def factory(identification: str = "12345678", **kwargs) -> User:
        return User.objects.create(
            first_name=kwargs.pop("first_name", "Ana"),
            last_name=kwargs.pop("last_name", "Pérez"),
            identification=identification,
            phone=kwargs.pop("phone", "3001234567"),
            correo=kwargs.pop("correo", f"{identification}@example.com"),
            password_hash=make_password(VALID_PASSWORD),
            is_administrador=kwargs.pop("is_administrador", True),
            **kwargs,
        )

    return factory


@pytest.fixture
def user(make_user) -> User:
    """Crea la cuenta autenticada habitual."""
    return make_user()


@pytest.fixture
def login(client):
    """Autentica una cuenta con el endpoint real y deja el Bearer en el cliente."""

    def authenticate(account: User) -> APIClient:
        response = client.post(
            "/api/v1/auth/login/",
            {
                "identification": account.identification,
                "password": VALID_PASSWORD,
            },
            format="json",
        )
        assert response.status_code == 200
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['token']}")
        return client

    return authenticate


@pytest.fixture
def auth_client(login, user) -> APIClient:
    """Retorna un cliente autenticado con la cuenta habitual."""
    return login(user)
