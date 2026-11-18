# ruff: noqa: S105
"""Fixtures locales de IoT.

Propósito: cuentas y fincas reales sin ``pytest_plugins`` anidados (mismo
criterio que ``apps/inventario/tests/conftest.py``).
Contexto: las pruebas con base de datos solo deben ejecutarse mediante el
orquestador desechable (``tools/run_permissions_disposable.ps1 -Validate``).
"""
import pytest
from django.contrib.auth.hashers import make_password
from django.core.management import call_command

from apps.accounts.models import User
from apps.farms.models import FarmMembership, FarmRole
from apps.farms.services import create_farm
from apps.iot.models import Coordinator, SensorSource, SensorSourceRole


def make_source_uid(*, user, farm_id: int, coordinator_uid: str):
    """Reuse a neutral ORM source for tests focused on other domain rules."""
    coordinator = Coordinator.objects.get(farm_id=farm_id, uid=coordinator_uid)
    source = SensorSource.objects.filter(coordinator=coordinator, is_active=True).first()
    if source is None:
        source = SensorSource.objects.create(
            farm_id=farm_id,
            coordinator=coordinator,
            name="Fuente de prueba",
            role=SensorSourceRole.PRIMARY,
            created_by=user,
        )
    return source.uid


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
            # INT-02b: crear fincas exige ser administrador nombrado; quien pide
            # can_create_farms=False queda como cuenta registrada sin permiso.
            can_create_farms=(puede := kwargs.pop("can_create_farms", True)),
            email_verified=kwargs.pop("email_verified", True),
            is_administrador=kwargs.pop("is_administrador", puede),
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


@pytest.fixture(autouse=True)
def _truncate_transactional_history(request):
    """Limpiar con TRUNCATE antes del flush DELETE de pytest-django.

    Los triggers históricos rechazan DELETE; TRUNCATE no dispara triggers de
    fila. Resolver ``transactional_db`` aquí garantiza que este finalizador se
    ejecute antes del teardown de ``_django_db_helper``, que limpia con DELETE.
    """
    marker = request.node.get_closest_marker("django_db")
    transactional = "transactional_db" in request.fixturenames or (
        bool(marker)
        and (
            bool(marker.kwargs.get("transaction"))
            or bool(marker.args and marker.args[0])
        )
    )
    if not transactional:
        yield
        return
    request.getfixturevalue("transactional_db")
    yield
    call_command(
        "flush",
        verbosity=0,
        interactive=False,
        reset_sequences=True,
        inhibit_post_migrate=True,
    )
