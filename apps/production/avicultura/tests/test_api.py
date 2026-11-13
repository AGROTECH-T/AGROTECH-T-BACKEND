# ruff: noqa: S101
"""Contrato HTTP del galpón.

@author Cristian Deysdayr Jimenez
"""
import pytest
from django.utils import timezone
from apps.farms.services import create_farm
from apps.production.avicultura.services import houses as house_services
from apps.production.avicultura.services import lots as lot_services
def _root(farm_id: int) -> str:
    return f"/api/v1/avicultura/{farm_id}"
@pytest.mark.django_db
def test_listar_galpones_exige_sesion(client, user):
    """Sin token no se ven los galpones."""
    farm_id = create_farm(user=user, name="La Esperanza").farm_id
    assert client.get(f"{_root(farm_id)}/galpones/").status_code == 401
@pytest.mark.django_db
def test_crear_y_listar_galpon(login, user):
    """El alta devuelve el galpón con área y sin lote abierto."""
    farm_id = create_farm(user=user, name="La Esperanza").farm_id
    client = login(user)
    created = client.post(
        f"{_root(farm_id)}/galpones/",
        {"name": "Norte", "length_m": "8", "width_m": "5"},
        format="json",
    )
    assert created.status_code == 201
    body = created.json()
    assert body["name"] == "Norte"
    assert body["area_m2"] == "40"
    assert body["open_lot_id"] is None
    listed = client.get(f"{_root(farm_id)}/galpones/")
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == body["id"]
@pytest.mark.django_db
def test_ver_lote_exige_sesion_y_devuelve_ficha(client, login, user):
    """Sin token no hay ficha; con sesión se ve el lote creado."""
    farm_id = create_farm(user=user, name="La Esperanza").farm_id
    house = house_services.create_house(user=user, farm_id=farm_id, name="Norte")
    lot = lot_services.open_lot(
        user=user,
        farm_id=farm_id,
        house_id=house.id,
        code="L-002",
        arrived_at=timezone.now(),
        received=50,
        receiver_name="Ana Pérez",
    )
    assert client.get(f"{_root(farm_id)}/lotes/{lot.id}/").status_code == 401
    body = login(user).get(f"{_root(farm_id)}/lotes/{lot.id}/")
    assert body.status_code == 200
    assert body.json()["code"] == "L-002"
