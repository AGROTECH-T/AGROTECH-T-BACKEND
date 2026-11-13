# ruff: noqa: S101, S105, S106
"""Pruebas de acceso y del nombramiento de administradores.

Propósito: una cuenta nueva solo ve Inicio; el superadmin nombra al administrador.
Contexto: el administrador ya puede crear fincas y trabajadores.
"""
import pytest
from django.contrib.auth.hashers import make_password
from rest_framework.test import APIClient

from apps.accounts.errors import AppError
from apps.accounts.models import User
from apps.accounts.services.access import cuenta_publica, modulos_visibles
from apps.accounts.services.platform_admin import (
    crear_administrador,
    listar_administradores,
    promover_administrador,
)
from apps.accounts.tests.constants import VALID_CREDENTIAL
from apps.farms.models import Farm, FarmMembership, FarmRole

CLAVE = "Secure1!"
ADMINS = "/api/v1/configuracion/administradores/"


@pytest.fixture
def client() -> APIClient:
    """Cliente HTTP de DRF."""
    return APIClient()


@pytest.fixture
def superadmin(db) -> User:
    """Cuenta sembrada que nombra administradores."""
    return User.objects.create(
        first_name="Super",
        last_name="Admin",
        identification="1000000001",
        correo="super@example.com",
        password_hash=make_password(CLAVE),
        is_superadmin=True,
    )


@pytest.fixture
def super_client(client, superadmin) -> APIClient:
    """Sesión del superadmin."""
    login = client.post(
        "/api/v1/auth/login/",
        {"identification": superadmin.identification, "password": CLAVE},
        format="json",
    )
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['token']}")
    return client


@pytest.fixture
def auth_client(client, user) -> APIClient:
    """Sesión de una cuenta recién registrada."""
    login = client.post(
        "/api/v1/auth/login/",
        {"identification": user.identification, "password": VALID_CREDENTIAL},
        format="json",
    )
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['token']}")
    return client


def test_cuenta_nueva_solo_ve_inicio(user):
    """El registro no abre módulos."""
    assert user.is_administrador is False
    assert modulos_visibles(user) == ["home"]
    payload = cuenta_publica(user)
    assert payload["is_superadmin"] is False
    assert payload["is_administrador"] is False
    assert payload["modulos"] == ["home"]


def test_superadmin_ve_configuracion(superadmin):
    """El superadmin entra a Configuración para nombrar administradores."""
    assert modulos_visibles(superadmin) == ["home", "configuracion"]


def test_administrador_ve_todos_los_modulos(user):
    """El administrador nombrado opera fincas, inventario, galpón y roles."""
    user.is_administrador = True
    user.save(update_fields=["is_administrador"])
    assert modulos_visibles(user) == [
        "home",
        "fincas",
        "inventario",
        "galpon",
        "configuracion",
    ]


@pytest.mark.django_db
def test_trabajador_ve_modulos_de_finca(user):
    """Un trabajador creado por el administrador entra a su finca."""
    farm = Farm.objects.create(name="El Roble")
    FarmMembership.objects.create(farm=farm, user=user, role=FarmRole.OPERADOR)
    assert modulos_visibles(user) == ["home", "fincas", "inventario", "galpon"]


@pytest.mark.django_db
def test_me_entrega_modulos_de_cuenta_nueva(auth_client):
    """El perfil de sesión trae las banderas y el menú permitido."""
    response = auth_client.get("/api/v1/auth/me/")
    assert response.status_code == 200
    assert response.data["modulos"] == ["home"]
    assert response.data["is_superadmin"] is False
    assert response.data["is_administrador"] is False


@pytest.mark.django_db
def test_crear_administrador_exige_superadmin(user):
    """Una cuenta normal no nombra administradores."""
    with pytest.raises(AppError) as error:
        crear_administrador(
            actor=user,
            datos={
                "first_name": "Luis",
                "last_name": "Mora",
                "identification": "22222222",
                "correo": "luis@example.com",
                "password": CLAVE,
            },
        )
    assert error.value.status_code == 403


@pytest.mark.django_db
def test_superadmin_crea_administrador(superadmin):
    """La cuenta nueva nace administradora y con todos los módulos."""
    ficha = crear_administrador(
        actor=superadmin,
        datos={
            "first_name": "Luis",
            "last_name": "Mora",
            "identification": "22222222",
            "correo": "luis@example.com",
            "password": CLAVE,
        },
    )
    creado = User.objects.get(identification="22222222")
    assert creado.is_administrador is True
    assert creado.is_superadmin is False
    assert ficha["is_administrador"] is True
    assert modulos_visibles(creado)[-1] == "configuracion"


@pytest.mark.django_db
def test_promueve_cuenta_registrada(superadmin, user):
    """El superadmin nombra administrador a quien ya se había registrado."""
    ficha = promover_administrador(actor=superadmin, user_id=user.id)
    user.refresh_from_db()
    assert user.is_administrador is True
    assert ficha["id"] == user.id


@pytest.mark.django_db
def test_no_promueve_al_superadmin(superadmin):
    """El superadmin no se convierte en administrador de finca por este flujo."""
    with pytest.raises(AppError) as error:
        promover_administrador(actor=superadmin, user_id=superadmin.id)
    assert error.value.status_code == 400


@pytest.mark.django_db
def test_listar_separa_pendientes_y_admins(superadmin, user):
    """Las cuentas en espera no se mezclan con las ya nombradas."""
    user.is_administrador = True
    user.save(update_fields=["is_administrador"])
    pendiente = User.objects.create(
        first_name="Eva",
        last_name="Rios",
        identification="77777777",
        correo="eva@example.com",
        password_hash=make_password(CLAVE),
    )
    data = listar_administradores(superadmin)
    assert [item["id"] for item in data["pendientes"]] == [pendiente.id]
    assert [item["id"] for item in data["administradores"]] == [user.id]


@pytest.mark.django_db
def test_api_crear_administrador(super_client):
    """El endpoint crea al administrador y lo deja listo para usar la app."""
    response = super_client.post(
        ADMINS,
        {
            "first_name": "Luis",
            "last_name": "Mora",
            "identification": "22222222",
            "correo": "luis@example.com",
            "password": CLAVE,
        },
        format="json",
    )
    assert response.status_code == 201
    assert response.data["is_administrador"] is True


@pytest.mark.django_db
def test_api_promover_cuenta(super_client, user):
    """El endpoint nombra a una cuenta que estaba en espera."""
    response = super_client.post(f"{ADMINS}{user.id}/promover/", format="json")
    assert response.status_code == 200
    user.refresh_from_db()
    assert user.is_administrador is True


@pytest.mark.django_db
def test_api_rechaza_cuenta_sin_poder(auth_client):
    """Sin ser superadmin la API responde 403."""
    response = auth_client.get(ADMINS)
    assert response.status_code == 403
