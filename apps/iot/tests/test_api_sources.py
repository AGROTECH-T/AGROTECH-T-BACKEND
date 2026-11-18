# ruff: noqa: S101
"""REST contract and farm authorization for physical sensor sources."""

import pytest
from rest_framework.test import APIClient

from apps.iot import services

pytestmark = pytest.mark.django_db


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def test_source_api_create_list_and_deactivate(admin, operator, farm_id):
    coordinator = services.create_coordinator(user=admin, farm_id=farm_id, name="C")
    client = _client(admin)
    url = f"/api/v1/iot/farms/{farm_id}/sensor-sources/"
    response = client.post(
        url,
        {
            "coordinator_uid": coordinator.uid,
            "name": "Principal",
            "model": "BME680",
            "role": "primary",
            "description": "Componente principal",
        },
        format="json",
    )

    assert response.status_code == 201
    uid = response.data["uid"]
    assert response.data["role"] == "primary"
    assert response.data["manufacturer"] is None
    assert "farm" not in response.data and "created_by" not in response.data

    listed = _client(operator).get(url)
    assert listed.status_code == 200
    assert listed.data["count"] == 1
    assert listed.data["results"][0]["uid"] == uid

    deactivated = client.post(f"{url}{uid}/deactivate/", {"reason": "reemplazo"}, format="json")
    assert deactivated.status_code == 200
    assert deactivated.data["is_active"] is False


def test_source_api_denies_non_admin_and_hides_other_farms(
    admin, operator, other_admin, farm_id, other_farm_id
):
    coordinator = services.create_coordinator(user=admin, farm_id=farm_id, name="C")
    url = f"/api/v1/iot/farms/{farm_id}/sensor-sources/"
    denied = _client(operator).post(
        url,
        {"coordinator_uid": coordinator.uid, "name": "No", "role": "primary"},
        format="json",
    )
    assert denied.status_code == 403

    foreign_read = _client(other_admin).get(url)
    assert foreign_read.status_code == 404
    own_read = _client(other_admin).get(
        f"/api/v1/iot/farms/{other_farm_id}/sensor-sources/"
    )
    assert own_read.status_code == 200


def test_source_creation_rejects_client_supplied_uid(admin, farm_id):
    coordinator = services.create_coordinator(user=admin, farm_id=farm_id, name="C")
    response = _client(admin).post(
        f"/api/v1/iot/farms/{farm_id}/sensor-sources/",
        {
            "uid": "00000000-0000-0000-0000-000000000001",
            "coordinator_uid": coordinator.uid,
            "name": "Fuente",
            "role": "primary",
        },
        format="json",
    )
    assert response.status_code == 400


def test_sensor_list_exposes_null_source_for_legacy_sensor(admin, farm_id):
    from apps.iot.identifiers import IdentifierKind, generate_identifier
    from apps.iot.models import Coordinator, Sensor

    coordinator = services.create_coordinator(user=admin, farm_id=farm_id, name="C")
    Sensor.objects.create(
        uid=generate_identifier(IdentifierKind.SENSOR),
        farm_id=farm_id,
        coordinator=Coordinator.objects.get(pk=coordinator.pk),
        channel=1,
        active_channel=1,
        metric="temperature",
    )

    response = _client(admin).get(f"/api/v1/iot/farms/{farm_id}/sensors/")

    assert response.status_code == 200
    assert response.data["results"][0]["source"] is None
