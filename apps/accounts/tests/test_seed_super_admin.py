# ruff: noqa: S101, S105, S106
"""Pruebas del seeder de super administrador.

Propósito: cubrir alta, repetición y errores de datos del comando.
Contexto: esa cuenta solo nombra administradores; no abre fincas.
"""
import pytest
from django.contrib.auth.hashers import check_password
from django.core.management import call_command
from django.core.management.base import CommandError
from rest_framework.serializers import ValidationError

from apps.accounts.models import User
from apps.accounts.super_admin_seed import SeedSuperAdminError, _texto, seed_super_admin
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
def test_comando_usa_entorno_cuando_faltan_argumentos(monkeypatch):
    """El manage.py toma documento y clave de las variables de entorno."""
    monkeypatch.setenv("SEED_SUPERADMIN_IDENTIFICATION", "33333333")
    monkeypatch.setenv("SEED_SUPERADMIN_PASSWORD", CLAVE)

    call_command("seed_super_admin")

    user = User.objects.get(identification="33333333")
    assert user.is_superadmin is True
    assert user.is_administrador is False


def test_mensaje_de_validacion_en_diccionario():
    """Si el serializer agrupa el error, se toma el primer texto."""
    assert _texto(ValidationError({"identification": ["documento invalido"]})) == (
        "documento invalido"
    )


@pytest.mark.django_db
def test_comando_sin_clave_en_cuenta_nueva_falla():
    """El comando corta con error si no hay clave para una cuenta nueva."""
    with pytest.raises(CommandError, match="contraseña es obligatoria"):
        call_command("seed_super_admin", identification="44444444")
