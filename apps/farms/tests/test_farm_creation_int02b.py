# ruff: noqa: S101
"""Pruebas de INT-02b: quién puede crear fincas.

Regla: crea fincas el superadmin, o el administrador de plataforma que el
superadmin nombró y que tiene el correo verificado. Una cuenta registrada,
aunque haya verificado su correo, no crea nada hasta ser nombrada. La
excepción de primera finca (F10.2) queda desactivada.
"""
import pytest
from rest_framework.test import APIClient

from apps.accounts.errors import AppError
from apps.accounts.services.platform_admin import promover_administrador
from apps.farms import services
from apps.farms.capabilities import can_create_farms, can_create_first_farm
from apps.farms.models import Farm, FarmMembership, FarmRole
from apps.farms.tests.conftest import VALID_PASSWORD
from apps.trazabilidad.models import AuditEvent, EventType

pytestmark = pytest.mark.django_db


def _client(account) -> APIClient:
    client = APIClient()
    response = client.post(
        "/api/v1/auth/login/",
        {"identification": account.identification, "password": VALID_PASSWORD},
        format="json",
    )
    assert response.status_code == 200
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['token']}")
    return client


def _registrada(make_user, identification="11111111", **kwargs):
    """Cuenta registrada y verificada, sin nombramiento del superadmin."""
    return make_user(identification, is_administrador=False, **kwargs)


def test_cuenta_verificada_sin_nombrar_no_crea_fincas(make_user):
    account = _registrada(make_user)
    response = _client(account).post("/api/v1/farms/", {"name": "Mía"}, format="json")
    assert response.status_code == 403
    assert response.data == {"error": services.FARM_CREATION_FORBIDDEN}
    assert not Farm.objects.exists()
    assert not AuditEvent.objects.filter(event_type=EventType.FARM_CREATION_GRANTED).exists()


def test_me_no_ofrece_primera_finca(make_user):
    account = _registrada(make_user, can_create_farms=False)
    me = _client(account).get("/api/v1/auth/me/").data
    assert me["can_create_farms"] is False
    assert me["can_create_first_farm"] is False


def test_bandera_sola_no_basta(make_user):
    account = _registrada(make_user, can_create_farms=True)
    assert can_create_farms(account) is False
    with pytest.raises(AppError) as error:
        services.create_farm(user=account, name="X")
    assert error.value.status_code == 403


def test_administrador_sin_correo_verificado_es_rechazado(make_user):
    account = make_user("22222222", is_administrador=True, email_verified=False)
    with pytest.raises(AppError) as error:
        services.create_farm(user=account, name="X")
    assert error.value.status_code == 403
    assert error.value.message == services.FARM_CREATION_EMAIL_UNVERIFIED
    assert not Farm.objects.exists()


def test_administrador_verificado_crea_finca(make_user):
    account = make_user("33333333", is_administrador=True)
    membership = services.create_farm(user=account, name="La Esperanza")
    assert membership.role == FarmRole.ADMINISTRADOR
    assert can_create_first_farm(account) is False


def test_superadmin_crea_aunque_no_haya_verificado(make_user):
    account = make_user("44444444", is_superadmin=True, is_administrador=False, email_verified=False)
    membership = services.create_farm(user=account, name="Central")
    assert membership.role == FarmRole.ADMINISTRADOR


def test_cuenta_nombrada_por_superadmin_ya_puede_crear(make_user):
    superadmin = make_user("55555555", is_superadmin=True, is_administrador=False)
    account = _registrada(make_user, "66666666")
    assert can_create_farms(account) is False
    promover_administrador(actor=superadmin, user_id=account.pk)
    account.refresh_from_db()
    assert can_create_farms(account) is True
    services.create_farm(user=account, name="Nombrada")
    assert FarmMembership.objects.filter(user=account, role=FarmRole.ADMINISTRADOR).exists()
