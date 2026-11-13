# ruff: noqa: S101, S105, S106
"""Pruebas del seeder de super administrador.

Propósito: cubrir alta, repetición y errores de datos del comando.
Contexto: esa cuenta solo nombra administradores; no abre fincas.
"""
import pytest
from django.contrib.auth.hashers import check_password
from django.core.management import call_command
from rest_framework.serializers import ValidationError

from apps.accounts.models import User
from apps.accounts.seeders import USUARIOS_INICIALES
from apps.accounts.super_admin_seed import (
    SeedSuperAdminError,
    _texto,
    seed_super_admin,
    seed_usuario,
    seed_usuarios_iniciales,
)
from apps.farms.models import Farm, FarmMembership

CLAVE = "Secure1!"
DOCUMENTO = "1000000001"


@pytest.fixture(autouse=True)
def sin_clave_de_entorno(monkeypatch):
    """Evita que un .env local filtre la clave al comando."""
    monkeypatch.delenv("SEED_SUPERADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("SEED_SUPERADMIN_IDENTIFICATION", raising=False)


@pytest.mark.django_db
def test_crea_superadmin_sin_finca():
    """La siembra deja la cuenta de plataforma, no una finca."""
    user = seed_super_admin(
        identification=DOCUMENTO,
        password=CLAVE,
        correo="super@example.com",
        phone="3001234567",
    )

    assert user.identification == DOCUMENTO
    assert user.is_superadmin is True
    assert user.is_administrador is False
    assert check_password(CLAVE, user.password_hash)
    assert Farm.objects.count() == 0
    assert FarmMembership.objects.count() == 0


@pytest.mark.django_db
def test_segunda_corrida_no_duplica():
    """Volver a sembrar no crea otra cuenta."""
    seed_super_admin(identification=DOCUMENTO, password=CLAVE)
    seed_super_admin(identification=DOCUMENTO, password=CLAVE)

    assert User.objects.filter(identification=DOCUMENTO).count() == 1
    assert User.objects.get(identification=DOCUMENTO).is_superadmin is True


@pytest.mark.django_db
def test_cuenta_nueva_sin_clave_falla():
    """Sin contraseña no se inventa una cuenta."""
    with pytest.raises(SeedSuperAdminError, match="contraseña es obligatoria"):
        seed_super_admin(identification=DOCUMENTO, password=None)


@pytest.mark.django_db
def test_clave_debil_se_rechaza():
    """La siembra exige la misma fortaleza que el registro."""
    with pytest.raises(SeedSuperAdminError, match="contraseña debe tener"):
        seed_super_admin(identification=DOCUMENTO, password="secreto")


@pytest.mark.django_db
def test_cuenta_existente_pasa_a_superadmin(user):
    """Si la cuenta ya existía, queda como superadmin y no como administrador."""
    seed_super_admin(
        identification=user.identification,
        password=CLAVE,
        first_name="Ana",
        last_name="Perez",
        phone="3009999999",
    )

    user.refresh_from_db()
    assert user.is_superadmin is True
    assert user.is_administrador is False
    assert user.phone == "3009999999"
    assert User.objects.filter(identification=user.identification).count() == 1


@pytest.mark.django_db
def test_identificacion_invalida_se_rechaza():
    """Un documento corto no entra a la base."""
    with pytest.raises(SeedSuperAdminError, match="identificacion"):
        seed_super_admin(identification="12", password=CLAVE)


@pytest.mark.django_db
def test_comando_siembra_usuarios_del_archivo():
    """El arranque crea las cuentas definidas en seeders.py."""
    inicial = USUARIOS_INICIALES[0]

    call_command("seed_usuarios")

    user = User.objects.get(identification=inicial["identification"])
    assert user.is_superadmin is True
    assert user.is_administrador is False
    assert user.first_name == inicial["first_name"]
    assert check_password(inicial["password"], user.password_hash)


@pytest.mark.django_db
def test_seeders_repite_sin_duplicar():
    """Volver a sembrar el archivo no crea otra cuenta."""
    seed_usuarios_iniciales()
    seed_usuarios_iniciales()

    assert User.objects.filter(
        identification=USUARIOS_INICIALES[0]["identification"]
    ).count() == 1


@pytest.mark.django_db
def test_seed_usuario_respeta_rol_de_administrador():
    """Una fila del seeder puede dejar administrador sin ser superadmin."""
    user = seed_usuario(
        identification="55555555",
        password=CLAVE,
        first_name="Ana",
        last_name="Perez",
        is_superadmin=False,
        is_administrador=True,
    )

    assert user.is_superadmin is False
    assert user.is_administrador is True


def test_mensaje_de_validacion_en_diccionario():
    """Si el serializer agrupa el error, se toma el primer texto."""
    assert _texto(ValidationError({"identification": ["documento invalido"]})) == (
        "documento invalido"
    )


@pytest.mark.django_db
def test_comando_viejo_delega_al_archivo():
    """seed_super_admin sigue sembrando las cuentas del archivo."""
    call_command("seed_super_admin")

    assert User.objects.filter(
        identification=USUARIOS_INICIALES[0]["identification"]
    ).exists()
