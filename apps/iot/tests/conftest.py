# ruff: noqa: S105
"""Fixtures locales de IoT.

Propósito: cuentas y fincas reales sin ``pytest_plugins`` anidados (mismo
criterio que ``apps/inventario/tests/conftest.py``).
Contexto: las pruebas con base de datos solo deben ejecutarse mediante el
orquestador desechable (``tools/run_permissions_disposable.ps1 -Validate``).
"""
import pytest
from django.contrib.auth.hashers import make_password

from apps.accounts.models import User
from apps.farms.models import FarmMembership, FarmRole
from apps.farms.services import create_farm


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
            email_verified=kwargs.pop("email_verified", True),
            **kwargs,
        )

    return factory


@pytest.fixture
def admin(make_user) -> User:
    """Administrador de la finca principal."""
    return make_user("11111111")


@pytest.fixture
def farm_id(admin) -> int:
    """Finca principal, creada por ``admin``."""
    return create_farm(user=admin, name="Finca IoT").farm_id


@pytest.fixture
def other_admin(make_user) -> User:
    """Administrador de otra finca."""
    return make_user("99999999")


@pytest.fixture
def other_farm_id(other_admin) -> int:
    """Finca ajena, creada por ``other_admin``."""
    return create_farm(user=other_admin, name="Finca ajena").farm_id


@pytest.fixture
def join(make_user):
    """Agrega una cuenta con un rol a una finca."""

    def factory(farm_id: int, role: str, identification: str) -> User:
        member = make_user(identification)
        FarmMembership.objects.create(farm_id=farm_id, user=member, role=role)
        return member

    return factory


@pytest.fixture
def operator(join, farm_id) -> User:
    """Operador de la finca principal."""
    return join(farm_id, FarmRole.OPERADOR, "22222222")


@pytest.fixture
def viewer(join, farm_id) -> User:
    """Cuenta de consulta de la finca principal."""
    return join(farm_id, FarmRole.CONSULTA, "33333333")
