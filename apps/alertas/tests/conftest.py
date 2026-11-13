# ruff: noqa: S105
"""Fixtures de avisos.

Propósito: cuenta y catálogo iguales a los de inventario.
@author Cristian Deysdayr Jimenez
"""
import pytest
from django.contrib.auth.hashers import make_password

from apps.accounts.models import User
from apps.inventario.seed_catalog import seed_catalog


@pytest.fixture(autouse=True)
def _catalog(db):
    seed_catalog()


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
            **kwargs,
        )

    return factory


@pytest.fixture
def user(make_user) -> User:
    """Cuenta habitual."""
    return make_user()
