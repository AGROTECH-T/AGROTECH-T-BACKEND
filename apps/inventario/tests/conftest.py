# ruff: noqa: S105
"""Fixtures locales de inventario.

Propósito: cuenta de prueba sin pytest_plugins anidados.
@author Cristian Deysdayr Jimenez
"""
import pytest
from django.contrib.auth.hashers import make_password

from apps.accounts.models import User


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
            **kwargs,
        )

    return factory


@pytest.fixture
def user(make_user) -> User:
    """Cuenta habitual para las pruebas de inventario."""
    return make_user()
