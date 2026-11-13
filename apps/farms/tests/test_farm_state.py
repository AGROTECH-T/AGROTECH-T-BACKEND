"""Pruebas de los campos de estado de finca en la respuesta de Farm."""
import pytest

from apps.farms import services
from apps.farms.models import Farm, FarmMembership, FarmRole

pytestmark = pytest.mark.django_db


def test_active_farm_response_exposes_active_state(auth_client, user):
    farm = Farm.objects.create(name="Activa")
    FarmMembership.objects.create(farm=farm, user=user, role=FarmRole.ADMINISTRADOR)
    response = auth_client.get(f"/api/v1/farms/{farm.pk}/")
    assert response.status_code == 200
    assert response.data["is_active"] is True
    assert response.data["deactivated_at"] is None


def test_deactivated_farm_response_exposes_state(auth_client, user):
    farm = Farm.objects.create(name="Inactiva")
    FarmMembership.objects.create(farm=farm, user=user, role=FarmRole.ADMINISTRADOR)
    services.deactivate_farm(user=user, farm_id=farm.pk)
    response = auth_client.get(f"/api/v1/farms/{farm.pk}/")
    assert response.status_code == 200
    assert response.data["is_active"] is False
    assert response.data["deactivated_at"] is not None


def test_response_keeps_existing_fields(auth_client, user):
    farm = Farm.objects.create(name="Campos")
    FarmMembership.objects.create(farm=farm, user=user, role=FarmRole.ADMINISTRADOR)
    response = auth_client.get(f"/api/v1/farms/{farm.pk}/")
    assert response.data["id"] == farm.pk
    assert response.data["name"] == "Campos"
    assert response.data["role"] == "administrador"
    assert "created_at" in response.data
    assert "updated_at" in response.data


def test_response_excludes_deactivated_by(auth_client, user):
    farm = Farm.objects.create(name="SinActor")
    FarmMembership.objects.create(farm=farm, user=user, role=FarmRole.ADMINISTRADOR)
    services.deactivate_farm(user=user, farm_id=farm.pk)
    response = auth_client.get(f"/api/v1/farms/{farm.pk}/")
    assert "deactivated_by" not in response.data
    assert "deactivated_by_id" not in response.data


def test_detail_authorization_unchanged(login, make_user):
    admin = make_user("11111111")
    operator = make_user("22222222")
    farm = Farm.objects.create(name="Auth")
    FarmMembership.objects.create(farm=farm, user=admin, role=FarmRole.ADMINISTRADOR)
    FarmMembership.objects.create(farm=farm, user=operator, role=FarmRole.OPERADOR)
    services.deactivate_farm(user=admin, farm_id=farm.pk)
    assert login(admin).get(f"/api/v1/farms/{farm.pk}/").status_code == 200
    assert login(operator).get(f"/api/v1/farms/{farm.pk}/").status_code == 404
