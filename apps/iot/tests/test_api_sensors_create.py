# ruff: noqa: S101
"""BUILD-IOT-AUTHORIZATION-API-01: C-4, ``POST /api/v1/iot/farms/{id}/sensors/``.

``create_sensor`` aplica las reglas de dominio (canal, métrica, fuente activa
del mismo coordinador y finca, canal libre). Aquí se verifica el contrato HTTP.
"""

import uuid

import pytest
from rest_framework.test import APIClient

from apps.farms.models import FarmMembership
from apps.iot import services
from apps.iot.catalog import metric_unit
from apps.iot.models import Sensor
from apps.trazabilidad.models import AuditEvent, EventType

pytestmark = pytest.mark.django_db


def _client(user=None):
    client = APIClient()
    if user is not None:
        client.force_authenticate(user=user)
    return client


def _source(user, farm_id, coordinator, name="Principal"):
    return services.create_sensor_source(
        user=user, farm_id=farm_id, coordinator_uid=coordinator.uid, name=name, role="primary"
    )


@pytest.fixture
def setup(admin, farm_id):
    coordinator = services.create_coordinator(user=admin, farm_id=farm_id, name="C")
    source = _source(admin, farm_id, coordinator)
    return coordinator, source


def _url(farm_id):
    return f"/api/v1/iot/farms/{farm_id}/sensors/"


def _body(coordinator, source, **overrides):
    body = {
        "coordinator_uid": coordinator.uid,
        "source_uid": str(source.uid),
        "channel": 1,
        "metric": "temperature",
    }
    body.update(overrides)
    return body


def _post(user, farm_id, body):
    return _client(user).post(_url(farm_id), body, format="json")


@pytest.mark.parametrize("metric", ["temperature", "relative_humidity"])
def test_admin_registers_sensor_with_catalog_unit_and_audit(admin, farm_id, setup, metric):
    coordinator, source = setup
    response = _post(admin, farm_id, _body(coordinator, source, metric=metric))
    assert response.status_code == 201
    data = response.data
    assert data["uid"].startswith("SN-")
    assert data["channel"] == 1 and data["metric"] == metric
    assert data["unit"] == metric_unit(metric)  # del catálogo
    assert data["coordinator_uid"] == coordinator.uid
    assert str(data["source"]["uid"]) == str(source.uid)
    assert data["is_active"] is True
    assert "farm" not in data and "created_by" not in data and "active_channel" not in data
    # La fuente (servicio existente) también audita IOT_SENSOR_CREATED con otro
    # entity_type; aquí solo cuenta el alta del sensor lógico.
    events = AuditEvent.objects.filter(
        event_type=EventType.IOT_SENSOR_CREATED, entity_type="iot_sensor"
    )
    assert events.count() == 1
    assert events.get().entity_id == data["uid"]


def test_unauthenticated_cannot_register(farm_id, setup):
    coordinator, source = setup
    assert _post(None, farm_id, _body(coordinator, source)).status_code in (401, 403)
    assert not Sensor.objects.exists()


def test_member_without_admin_role_gets_403(operator, farm_id, setup):
    coordinator, source = setup
    assert _post(operator, farm_id, _body(coordinator, source)).status_code == 403
    assert not Sensor.objects.exists()


def test_inactive_membership_and_outsider_get_404(operator, other_admin, farm_id, setup):
    coordinator, source = setup
    FarmMembership.objects.filter(user=operator).update(status="revoked")
    assert _post(operator, farm_id, _body(coordinator, source)).status_code == 404
    # Un no miembro recibe 404 incluso con datos inválidos.
    assert _post(other_admin, farm_id, {"bogus": True}).status_code == 404
    assert not Sensor.objects.exists()


def test_unknown_coordinator_gets_404(admin, farm_id, setup):
    _coordinator, source = setup
    body = _body(_coordinator, source, coordinator_uid="CO-ZZZZZZZZZZ")
    assert _post(admin, farm_id, body).status_code == 404


def test_unknown_source_gets_404(admin, farm_id, setup):
    coordinator, source = setup
    body = _body(coordinator, source, source_uid=str(uuid.uuid4()))
    assert _post(admin, farm_id, body).status_code == 404


def test_source_of_another_farm_gets_404(admin, other_admin, farm_id, other_farm_id, setup):
    coordinator, _source_ = setup
    foreign_coord = services.create_coordinator(user=other_admin, farm_id=other_farm_id, name="Y")
    foreign_source = _source(other_admin, other_farm_id, foreign_coord)
    assert _post(admin, farm_id, _body(coordinator, foreign_source)).status_code == 404
    # Coordinador ajeno con fuente ajena, desde nuestra finca: tampoco se revela.
    assert _post(admin, farm_id, _body(foreign_coord, foreign_source)).status_code == 404
    assert not Sensor.objects.exists()


def test_source_of_another_coordinator_gets_404(admin, farm_id, setup):
    coordinator, _src = setup
    other_coord = services.create_coordinator(user=admin, farm_id=farm_id, name="C2")
    other_source = _source(admin, farm_id, other_coord, name="Otra")
    assert _post(admin, farm_id, _body(coordinator, other_source)).status_code == 404


def test_inactive_source_gets_409(admin, farm_id, setup):
    coordinator, source = setup
    services.deactivate_sensor_source(
        user=admin, farm_id=farm_id, source_uid=source.uid, reason="retirada"
    )
    assert _post(admin, farm_id, _body(coordinator, source)).status_code == 409
    assert not Sensor.objects.exists()


def test_inactive_coordinator_gets_409(admin, farm_id, setup):
    coordinator, source = setup
    services.deactivate_coordinator(user=admin, farm_id=farm_id, uid=coordinator.uid, reason="baja")
    assert _post(admin, farm_id, _body(coordinator, source)).status_code == 409


def test_occupied_channel_gets_409(admin, farm_id, setup):
    coordinator, source = setup
    assert _post(admin, farm_id, _body(coordinator, source)).status_code == 201
    second = _post(admin, farm_id, _body(coordinator, source, metric="relative_humidity"))
    assert second.status_code == 409 and "error" in second.data
    assert Sensor.objects.filter(is_active=True).count() == 1


@pytest.mark.parametrize("channel", [True, False, -1, 65536, "uno", None, 1.5])
def test_invalid_channel_gets_400(admin, farm_id, setup, channel):
    coordinator, source = setup
    assert _post(admin, farm_id, _body(coordinator, source, channel=channel)).status_code == 400
    assert not Sensor.objects.exists()


@pytest.mark.parametrize("metric", ["pressure", "TEMPERATURE", ""])
def test_unknown_metric_gets_400(admin, farm_id, setup, metric):
    coordinator, source = setup
    assert _post(admin, farm_id, _body(coordinator, source, metric=metric)).status_code == 400


def test_unknown_field_and_missing_source_get_400(admin, farm_id, setup):
    coordinator, source = setup
    extra = _body(coordinator, source, active_channel=1)
    assert _post(admin, farm_id, extra).status_code == 400
    missing = _body(coordinator, source)
    missing.pop("source_uid")
    response = _post(admin, farm_id, missing)
    assert response.status_code == 400  # formato de error del proyecto: {"error": ...}
    bad_uuid = _body(coordinator, source, source_uid="no-uuid")
    assert _post(admin, farm_id, bad_uuid).status_code == 400
    assert not Sensor.objects.exists()


def test_existing_get_has_no_regression(admin, operator, farm_id, setup):
    coordinator, source = setup
    created = _post(admin, farm_id, _body(coordinator, source)).data
    listed = _client(operator).get(_url(farm_id))
    assert listed.status_code == 200
    assert [row["uid"] for row in listed.data["results"]] == [created["uid"]]
    assert listed.data["results"][0] == created
