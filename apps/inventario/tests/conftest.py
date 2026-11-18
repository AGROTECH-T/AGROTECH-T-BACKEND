# ruff: noqa: S105
"""Fixtures locales de inventario.

Propósito: cuenta de prueba sin pytest_plugins anidados.
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
def _isolated_state(monkeypatch):
    """Limpia el throttling entre pruebas y fija la clave de sesión."""
    cache.clear()
    monkeypatch.setenv("JWT_ENCRYPTION_KEY", base64.b64encode(b"k" * 32).decode())
    yield
    cache.clear()


@pytest.fixture
def client() -> APIClient:
    """Cliente HTTP de DRF sin credenciales."""
    return APIClient()


@pytest.fixture
def login(client):
    """Autentica una cuenta con el endpoint real y deja el Bearer puesto."""

    def authenticate(account: User) -> APIClient:
        response = client.post(
            "/api/v1/auth/login/",
            {"identification": account.identification, "password": VALID_PASSWORD},
            format="json",
        )
        assert response.status_code == 200
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['token']}")
        return client

    return authenticate


@pytest.fixture
def make_user(db):
    """Fábrica de cuentas válidas."""

    def factory(identification: str = "12345678", **kwargs) -> User:
        return User.objects.create(
            first_name=kwargs.pop("first_name", "Ana"),
            last_name=kwargs.pop("last_name", "Pérez"),
            identification=identification,
            phone=kwargs.pop("phone", "3001234567"),
            correo=kwargs.pop("correo", f"{identification}@example.com"),
            password_hash=make_password(kwargs.pop("password", "Secure1!")),
            can_create_farms=kwargs.pop("can_create_farms", True),
            # F10.2: crear fincas exige correo verificado en el servicio.
            email_verified=kwargs.pop("email_verified", True),
            # INT-02: rol de plataforma solo si la prueba lo pide.
            is_administrador=kwargs.pop("is_administrador", False),
            **kwargs,
        )

    return factory


@pytest.fixture
def user(make_user) -> User:
    """Cuenta habitual para las pruebas de inventario."""
    return make_user()
