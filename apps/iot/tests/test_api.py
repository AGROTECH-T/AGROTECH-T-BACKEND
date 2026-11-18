# ruff: noqa: S101
"""Pruebas de la API IoT de consulta y aislamiento por finca."""
import pytest
from rest_framework.test import APIClient

from apps.iot import services
from apps.iot.tests.conftest import make_source_uid

pytestmark = pytest.mark.django_db


def _client(user=None):
    client = APIClient()
    if user is not None:
        client.force_authenticate(user=user)
    return client


def test_iot_lists_require_authentication(farm_id):
    response = _client().get(f"/api/v1/iot/farms/{farm_id}/gateways/")
    assert response.status_code == 401


@pytest.mark.parametrize(
    ("resource", "service_method", "expected_field"),
    [
        ("gateways", services.create_gateway, "location_label"),
        ("coordinators", services.create_coordinator, "location_label"),
    ],
)
def test_device_lists_allow_member_and_use_closed_public_fields(
    admin, operator, farm_id, resource, service_method, expected_field
):
    device = service_method(user=admin, farm_id=farm_id, name="Dispositivo")
    response = _client(operator).get(f"/api/v1/iot/farms/{farm_id}/{resource}/")

    assert response.status_code == 200
    assert response.data["count"] == 1
    row = response.data["results"][0]
    assert set(row) == {
        "uid", "name", "location_label", "is_active", "created_at", "updated_at"
    }
    assert expected_field in row
    assert row["uid"] == device.uid
    assert "farm" not in row
    assert "credential" not in row


def test_sensor_list_returns_unit_and_coordinator_uid(admin, operator, farm_id):
    coordinator = services.create_coordinator(user=admin, farm_id=farm_id, name="C")
    sensor = services.create_sensor(
        user=admin,
        farm_id=farm_id,
        coordinator_uid=coordinator.uid,
        source_uid=make_source_uid(user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid),
        channel=1,
        metric="temperature",
    )

    response = _client(operator).get(f"/api/v1/iot/farms/{farm_id}/sensors/")

    assert response.status_code == 200
    assert response.data["results"] == [
        {
            "uid": sensor.uid,
            "channel": 1,
            "metric": "temperature",
            "unit": "°C",
            "is_active": True,
            "coordinator_uid": coordinator.uid,
            "source": {
                "uid": str(sensor.source.uid),
                "name": "Fuente de prueba",
                "model": None,
                "manufacturer": None,
                "role": "primary",
                "description": "",
            },
        }
    ]


def test_farm_isolation_and_missing_farm(admin, other_admin, farm_id, other_farm_id):
    services.create_gateway(user=admin, farm_id=farm_id, name="Privado")
    client = _client(other_admin)

    assert client.get(f"/api/v1/iot/farms/{farm_id}/gateways/").status_code == 404
    assert client.get("/api/v1/iot/farms/999999999/gateways/").status_code == 404
    assert client.get(f"/api/v1/iot/farms/{other_farm_id}/gateways/").status_code == 200


def test_user_without_membership_cannot_read(admin, farm_id, make_user):
    outsider = make_user("77777777")
    response = _client(outsider).get(f"/api/v1/iot/farms/{farm_id}/sensors/")
    assert response.status_code == 404


def test_page_size_is_bounded_and_pages_are_supported(admin, farm_id):
    for index in range(3):
        services.create_gateway(user=admin, farm_id=farm_id, name=f"G-{index}")

    response = _client(admin).get(f"/api/v1/iot/farms/{farm_id}/gateways/?page=2&page_size=2")

    assert response.status_code == 200
    assert response.data["count"] == 3
    assert len(response.data["results"]) == 1
    assert response.data["next"] is None


def test_invalid_page_is_rejected(admin, farm_id):
    response = _client(admin).get(f"/api/v1/iot/farms/{farm_id}/gateways/?page=invalid")
    assert response.status_code == 404


def test_query_endpoints_are_read_only(admin, farm_id):
    # VALIDATION-02: POST /gateways/ is the approved provisioning endpoint
    # (docs/modules/iot-aprovisionamiento-seguro.md, test_provisioning_api.py).
    # BUILD-IOT-AUTHORIZATION-API-01 (C-4): POST /sensors/ is now the approved
    # sensor registration endpoint (test_api_sensors_create.py). The remaining
    # query-only endpoint is readings/.
    for resource in ("readings",):
        response = _client(admin).post(
            f"/api/v1/iot/farms/{farm_id}/{resource}/", {"name": "No crear"}, format="json"
        )
        assert response.status_code == 405
