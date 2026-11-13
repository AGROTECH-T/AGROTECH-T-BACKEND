# ruff: noqa: S105
"""Fixtures del galpón.

Propósito: cuentas y sesión HTTP iguales a las de fincas.
@author Cristian Deysdayr Jimenez
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
    """Claves fijas solo para las pruebas."""
    cache.clear()
    monkeypatch.setenv("JWT_ENCRYPTION_KEY", base64.b64encode(b"k" * 32).decode())
    monkeypatch.setenv("OTP_HMAC_KEY", "test-otp-key")
    yield
    cache.clear()


@pytest.fixture
def client() -> APIClient:
    """Cliente HTTP sin credenciales."""
    return APIClient()


@pytest.fixture
def make_user(db):
    """Fábrica de cuentas con documento único."""

    def factory(identification: str = "12345678", **kwargs) -> User:
        return User.objects.create(
            first_name=kwargs.pop("first_name", "Ana"),
            last_name=kwargs.pop("last_name", "Pérez"),
            identification=identification,
            phone=kwargs.pop("phone", "3001234567"),
            correo=kwargs.pop("correo", f"{identification}@example.com"),
            password_hash=make_password(VALID_PASSWORD),
            **kwargs,
        )

    return factory


@pytest.fixture
def user(make_user) -> User:
    """Cuenta habitual del módulo."""
    return make_user()


@pytest.fixture
def login(client):
    """Deja el Bearer de una cuenta en el cliente."""

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
