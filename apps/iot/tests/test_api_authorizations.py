# ruff: noqa: S101
"""BUILD-IOT-AUTHORIZATION-API-01: C-1 (autorizar), C-2 (historial) y C-3 (revocar).

Las reglas de dominio las aplica ``apps.iot.services.authorizations``; aquí se
verifica el contrato HTTP, el orden de seguridad y el aislamiento entre fincas.
"""

import pytest
from rest_framework.test import APIClient

from apps.farms.models import FarmMembership
from apps.iot import services
from apps.iot.models import CoordinatorGateway
from apps.trazabilidad.models import AuditEvent, EventType

pytestmark = pytest.mark.django_db

FORBIDDEN_KEYS = {"id", "farm", "authorized_by", "revoked_by", "password", "secret", "key"}


def _client(user=None):
    client = APIClient()
    if user is not None:
        client.force_authenticate(user=user)
    return client


@pytest.fixture
def pair(admin, farm_id):
    coordinator = services.create_coordinator(user=admin, farm_id=farm_id, name="C")
    gateway = services.create_gateway(user=admin, farm_id=farm_id, name="G")
    return coordinator, gateway


def _url(farm_id, coordinator_uid):
    return f"/api/v1/iot/farms/{farm_id}/coordinators/{coordinator_uid}/gateways/"


def _revoke_url(farm_id, coordinator_uid, gateway_uid):
    return f"{_url(farm_id, coordinator_uid)}{gateway_uid}/revoke/"


def _authorize(user, farm_id, coordinator, gateway):
    return _client(user).post(
        _url(farm_id, coordinator.uid), {"gateway_uid": gateway.uid}, format="json"
    )


# -- C-1 ------------------------------------------------------------------------------


def test_admin_authorizes_pair_and_response_has_no_internals(admin, farm_id, pair):
    coordinator, gateway = pair
    response = _authorize(admin, farm_id, coordinator, gateway)
    assert response.status_code == 201
    assert response.data["coordinator_uid"] == coordinator.uid
    assert response.data["gateway_uid"] == gateway.uid
    assert response.data["active"] is True and response.data["revoked_at"] is None
    assert response.data["authorized_at"]
    assert not FORBIDDEN_KEYS & set(response.data)
    assert CoordinatorGateway.objects.filter(coordinator=coordinator, gateway=gateway).count() == 1
    events = AuditEvent.objects.filter(event_type=EventType.IOT_GATEWAY_AUTHORIZED)
    assert events.count() == 1
    assert events.get().entity_type == "iot_coordinator_gateway"


def test_unauthenticated_cannot_authorize(farm_id, pair):
    coordinator, gateway = pair
    response = _authorize(None, farm_id, coordinator, gateway)
    assert response.status_code in (401, 403)
    assert not CoordinatorGateway.objects.exists()


def test_member_without_admin_role_gets_403(operator, farm_id, pair):
    coordinator, gateway = pair
    response = _authorize(operator, farm_id, coordinator, gateway)
    assert response.status_code == 403
    assert not CoordinatorGateway.objects.exists()
    assert not AuditEvent.objects.filter(event_type=EventType.IOT_GATEWAY_AUTHORIZED).exists()


def test_inactive_membership_and_outsider_get_404(operator, other_admin, farm_id, pair):
    coordinator, gateway = pair
    FarmMembership.objects.filter(user=operator).update(status="revoked")
    assert _authorize(operator, farm_id, coordinator, gateway).status_code == 404
    assert _authorize(other_admin, farm_id, coordinator, gateway).status_code == 404
    assert not CoordinatorGateway.objects.exists()


def test_non_member_gets_404_even_with_invalid_body(other_admin, farm_id, pair):
    coordinator, _gateway = pair
    response = _client(other_admin).post(
        _url(farm_id, coordinator.uid), {"unexpected": "x"}, format="json"
    )
    assert response.status_code == 404


@pytest.mark.parametrize("bad", ["CO-ZZZZZZZZZZ", "no-es-un-uid"])
def test_unknown_coordinator_gets_404(admin, farm_id, pair, bad):
    _coordinator, gateway = pair
    response = _client(admin).post(_url(farm_id, bad), {"gateway_uid": gateway.uid}, format="json")
    assert response.status_code == 404


@pytest.mark.parametrize("bad", ["GW-ZZZZZZZZZZ", "basura"])
def test_unknown_gateway_gets_404(admin, farm_id, pair, bad):
    coordinator, _gateway = pair
    response = _client(admin).post(
        _url(farm_id, coordinator.uid), {"gateway_uid": bad}, format="json"
    )
    assert response.status_code == 404


def test_devices_of_another_farm_are_not_found(admin, other_admin, farm_id, other_farm_id, pair):
    coordinator, gateway = pair
    foreign_gateway = services.create_gateway(user=other_admin, farm_id=other_farm_id, name="X")
    foreign_coord = services.create_coordinator(user=other_admin, farm_id=other_farm_id, name="Y")
    assert _authorize(admin, farm_id, coordinator, foreign_gateway).status_code == 404
    assert _authorize(admin, farm_id, foreign_coord, gateway).status_code == 404
    # El admin ajeno tampoco puede usar su finca para tocar dispositivos de la nuestra.
    assert _authorize(other_admin, other_farm_id, foreign_coord, gateway).status_code == 404
    assert not CoordinatorGateway.objects.exists()


def test_duplicate_authorization_gets_409(admin, farm_id, pair):
    coordinator, gateway = pair
    assert _authorize(admin, farm_id, coordinator, gateway).status_code == 201
    second = _authorize(admin, farm_id, coordinator, gateway)
    assert second.status_code == 409 and "error" in second.data
    assert CoordinatorGateway.objects.filter(active_pair__isnull=False).count() == 1


@pytest.mark.django_db(transaction=True)
def test_concurrent_authorizations_keep_one_active_pair(admin, farm_id, pair):
    from apps.iot.tests.test_broker_identity_concurrency import _parallel

    coordinator, gateway = pair
    results = _parallel([lambda: _authorize(admin, farm_id, coordinator, gateway)] * 4)
    codes = sorted(getattr(r, "status_code", repr(r)) for r in results)
    assert codes == [201, 409, 409, 409], codes
    assert CoordinatorGateway.objects.filter(active_pair__isnull=False).count() == 1
    assert AuditEvent.objects.filter(event_type=EventType.IOT_GATEWAY_AUTHORIZED).count() == 1


def test_inactive_device_gets_409(admin, farm_id, pair):
    coordinator, gateway = pair
    services.deactivate_gateway(user=admin, farm_id=farm_id, uid=gateway.uid, reason="baja")
    assert _authorize(admin, farm_id, coordinator, gateway).status_code == 409
    assert not CoordinatorGateway.objects.exists()


def test_unknown_field_gets_400(admin, farm_id, pair):
    coordinator, gateway = pair
    response = _client(admin).post(
        _url(farm_id, coordinator.uid),
        {"gateway_uid": gateway.uid, "active_pair": 1},
        format="json",
    )
    assert response.status_code == 400
    assert not CoordinatorGateway.objects.exists()


# -- C-2 ------------------------------------------------------------------------------


def _history(user, farm_id, coordinator_uid, **params):
    return _client(user).get(_url(farm_id, coordinator_uid), params)


def test_history_lists_active_and_revoked(admin, viewer, farm_id, pair):
    coordinator, gateway = pair
    _authorize(admin, farm_id, coordinator, gateway)
    services.revoke_gateway_authorization(
        user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid, gateway_uid=gateway.uid
    )
    _authorize(admin, farm_id, coordinator, gateway)
    everything = _history(viewer, farm_id, coordinator.uid)  # lectura: no exige admin
    assert everything.status_code == 200
    rows = everything.data["results"]
    assert len(rows) == 2
    assert sorted(row["active"] for row in rows) == [False, True]
    for row in rows:
        assert not FORBIDDEN_KEYS & set(row)
        assert row["gateway_uid"] == gateway.uid
    only_active = _history(viewer, farm_id, coordinator.uid, include_revoked="false")
    assert [row["active"] for row in only_active.data["results"]] == [True]
    explicit = _history(viewer, farm_id, coordinator.uid, include_revoked="true")
    assert len(explicit.data["results"]) == 2


def test_history_invalid_parameter_gets_400(admin, farm_id, pair):
    coordinator, _gateway = pair
    response = _history(admin, farm_id, coordinator.uid, include_revoked="quizas")
    assert response.status_code == 400


def test_history_isolation_and_membership(admin, other_admin, other_farm_id, farm_id, pair):
    coordinator, _gateway = pair
    foreign = services.create_coordinator(user=other_admin, farm_id=other_farm_id, name="Y")
    assert _history(admin, farm_id, foreign.uid).status_code == 404
    assert _history(admin, other_farm_id, foreign.uid).status_code == 404
    outsider = _history(other_admin, farm_id, coordinator.uid, include_revoked="quizas")
    assert outsider.status_code == 404  # membresía antes de validar parámetros
    assert _history(None, farm_id, coordinator.uid).status_code in (401, 403)


# -- C-3 ------------------------------------------------------------------------------


def _revoke(user, farm_id, coordinator, gateway, body=None):
    return _client(user).post(
        _revoke_url(farm_id, coordinator.uid, gateway.uid), body or {}, format="json"
    )


def test_revoke_with_reason_keeps_history_and_audits(admin, farm_id, pair):
    coordinator, gateway = pair
    _authorize(admin, farm_id, coordinator, gateway)
    response = _revoke(admin, farm_id, coordinator, gateway, {"reason": "gateway sustituido"})
    assert response.status_code == 200
    assert response.data["active"] is False and response.data["revoked_at"]
    assert not FORBIDDEN_KEYS & set(response.data)
    row = CoordinatorGateway.objects.get(coordinator=coordinator, gateway=gateway)
    assert row.revoked_by_id == admin.pk and row.revoked_at is not None  # trazabilidad
    event = AuditEvent.objects.get(event_type=EventType.IOT_GATEWAY_AUTHORIZATION_REVOKED)
    assert event.reason == "gateway sustituido"
    history = _history(admin, farm_id, coordinator.uid)
    assert [r["active"] for r in history.data["results"]] == [False]  # no se borra


def test_revoke_without_reason(admin, farm_id, pair):
    coordinator, gateway = pair
    _authorize(admin, farm_id, coordinator, gateway)
    assert _revoke(admin, farm_id, coordinator, gateway).status_code == 200


def test_revoke_reason_too_long_gets_400(admin, farm_id, pair):
    coordinator, gateway = pair
    _authorize(admin, farm_id, coordinator, gateway)
    response = _revoke(admin, farm_id, coordinator, gateway, {"reason": "x" * 201})
    assert response.status_code == 400
    assert CoordinatorGateway.objects.get().is_active


def test_revoke_missing_and_second_revocation_get_404(admin, farm_id, pair):
    coordinator, gateway = pair
    assert _revoke(admin, farm_id, coordinator, gateway).status_code == 404
    _authorize(admin, farm_id, coordinator, gateway)
    assert _revoke(admin, farm_id, coordinator, gateway).status_code == 200
    assert _revoke(admin, farm_id, coordinator, gateway).status_code == 404
    assert (
        AuditEvent.objects.filter(event_type=EventType.IOT_GATEWAY_AUTHORIZATION_REVOKED).count()
        == 1
    )


def test_revoke_requires_admin(admin, operator, other_admin, farm_id, pair):
    coordinator, gateway = pair
    _authorize(admin, farm_id, coordinator, gateway)
    assert _revoke(operator, farm_id, coordinator, gateway).status_code == 403
    assert _revoke(other_admin, farm_id, coordinator, gateway).status_code == 404
    assert _revoke(None, farm_id, coordinator, gateway).status_code in (401, 403)
    assert CoordinatorGateway.objects.get().is_active


def test_revoke_unknown_field_gets_400(admin, farm_id, pair):
    coordinator, gateway = pair
    _authorize(admin, farm_id, coordinator, gateway)
    response = _revoke(admin, farm_id, coordinator, gateway, {"reason": "x", "cause": "y"})
    assert response.status_code == 400
