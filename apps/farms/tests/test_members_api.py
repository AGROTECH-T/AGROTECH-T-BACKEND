"""Pruebas de la API de gestión de integrantes."""
import pytest

from apps.farms import services
from apps.farms.models import FarmMembership, FarmRole, MembershipStatus
from apps.trazabilidad.models import AuditEvent, EventType

pytestmark = pytest.mark.django_db


def _farm(make_user, identification="11111111", name="Finca"):
    admin = make_user(identification)
    membership = services.create_farm(user=admin, name=name)
    return admin, membership.farm_id, membership


def _add(farm_id, user, role):
    return FarmMembership.objects.create(farm_id=farm_id, user=user, role=role)


# --- Listado de integrantes ---


def test_admin_can_list_members(login, make_user):
    admin, farm_id, _ = _farm(make_user)
    operator = make_user("22222222")
    _add(farm_id, operator, FarmRole.OPERADOR)
    response = login(admin).get(f"/api/v1/farms/{farm_id}/members/")
    assert response.status_code == 200
    assert response.data["count"] == 2  # admin + operador


def test_list_defaults_to_active_filter(login, make_user):
    admin, farm_id, _ = _farm(make_user)
    operator = make_user("22222222")
    m = _add(farm_id, operator, FarmRole.OPERADOR)
    services.revoke_member(user=admin, farm_id=farm_id, membership_id=m.pk)
    response = login(admin).get(f"/api/v1/farms/{farm_id}/members/")
    assert response.status_code == 200
    assert response.data["count"] == 1  # solo el admin activo


def test_list_status_all(login, make_user):
    admin, farm_id, _ = _farm(make_user)
    operator = make_user("22222222")
    m = _add(farm_id, operator, FarmRole.OPERADOR)
    services.revoke_member(user=admin, farm_id=farm_id, membership_id=m.pk)
    response = login(admin).get(f"/api/v1/farms/{farm_id}/members/?status=all")
    assert response.status_code == 200
    assert response.data["count"] == 2


def test_list_search_by_name(login, make_user):
    admin, farm_id, _ = _farm(make_user)
    operator = make_user("22222222", first_name="Carlos", last_name="Pérez")
    _add(farm_id, operator, FarmRole.OPERADOR)
    response = login(admin).get(f"/api/v1/farms/{farm_id}/members/?search=Carlos")
    assert response.status_code == 200
    assert response.data["count"] == 1
    assert response.data["results"][0]["name"] == "Carlos Pérez"


def test_non_admin_cannot_list_members(login, make_user):
    admin, farm_id, _ = _farm(make_user)
    operator = make_user("22222222")
    _add(farm_id, operator, FarmRole.OPERADOR)
    response = login(operator).get(f"/api/v1/farms/{farm_id}/members/")
    assert response.status_code == 403


def test_external_user_cannot_list_members(login, make_user):
    admin, farm_id, _ = _farm(make_user)
    outsider = make_user("99999999")
    response = login(outsider).get(f"/api/v1/farms/{farm_id}/members/")
    assert response.status_code == 404


# --- Detalle de membresía ---


def test_admin_can_see_any_member(login, make_user):
    admin, farm_id, _ = _farm(make_user)
    operator = make_user("22222222")
    m = _add(farm_id, operator, FarmRole.OPERADOR)
    response = login(admin).get(f"/api/v1/farms/{farm_id}/members/{m.pk}/")
    assert response.status_code == 200
    assert response.data["id"] == m.pk
    assert response.data["role"] == "operador"


def test_member_can_see_own_membership(login, make_user):
    admin, farm_id, _ = _farm(make_user)
    operator = make_user("22222222")
    m = _add(farm_id, operator, FarmRole.OPERADOR)
    response = login(operator).get(f"/api/v1/farms/{farm_id}/members/{m.pk}/")
    assert response.status_code == 200
    assert response.data["id"] == m.pk


def test_operator_cannot_see_other_member(login, make_user):
    admin, farm_id, _ = _farm(make_user)
    operator = make_user("22222222")
    _add(farm_id, operator, FarmRole.OPERADOR)
    consulta = make_user("33333333")
    m = _add(farm_id, consulta, FarmRole.CONSULTA)
    response = login(operator).get(f"/api/v1/farms/{farm_id}/members/{m.pk}/")
    assert response.status_code == 403


def test_member_response_excludes_personal_data(login, make_user):
    admin, farm_id, _ = _farm(make_user)
    operator = make_user("22222222")
    m = _add(farm_id, operator, FarmRole.OPERADOR)
    response = login(admin).get(f"/api/v1/farms/{farm_id}/members/{m.pk}/")
    data = response.data
    assert "name" in data
    assert "correo" not in data
    assert "email" not in data
    assert "phone" not in data
    assert "identification" not in data


# --- IDOR ---


def test_cannot_access_membership_of_other_farm(login, make_user):
    admin_a, farm_a, _ = _farm(make_user, identification="11111111", name="A")
    admin_b, farm_b, owner_b = _farm(make_user, identification="22222222", name="B")
    response = login(admin_a).get(f"/api/v1/farms/{farm_b}/members/{owner_b.pk}/")
    assert response.status_code == 404
    assert farm_a != farm_b


# --- Cambio de rol ---


def test_change_role_success(login, make_user):
    admin, farm_id, _ = _farm(make_user)
    operator = make_user("22222222")
    m = _add(farm_id, operator, FarmRole.OPERADOR)
    response = login(admin).patch(
        f"/api/v1/farms/{farm_id}/members/{m.pk}/",
        {"role": "consulta"},
        format="json",
    )
    assert response.status_code == 200
    assert response.data["role"] == "consulta"


def test_change_role_invalid_role(login, make_user):
    admin, farm_id, _ = _farm(make_user)
    operator = make_user("22222222")
    m = _add(farm_id, operator, FarmRole.OPERADOR)
    response = login(admin).patch(
        f"/api/v1/farms/{farm_id}/members/{m.pk}/",
        {"role": "propietario"},
        format="json",
    )
    assert response.status_code == 400


def test_change_role_non_admin_forbidden(login, make_user):
    admin, farm_id, _ = _farm(make_user)
    operator = make_user("22222222")
    _add(farm_id, operator, FarmRole.OPERADOR)
    consulta = make_user("33333333")
    m = _add(farm_id, consulta, FarmRole.CONSULTA)
    response = login(operator).patch(
        f"/api/v1/farms/{farm_id}/members/{m.pk}/",
        {"role": "operador"},
        format="json",
    )
    assert response.status_code == 403


def test_change_role_last_admin_conflict(login, make_user):
    admin, farm_id, owner = _farm(make_user)
    response = login(admin).patch(
        f"/api/v1/farms/{farm_id}/members/{owner.pk}/",
        {"role": "operador"},
        format="json",
    )
    assert response.status_code == 409


# --- Revocación ---


def test_revoke_success(login, make_user):
    admin, farm_id, _ = _farm(make_user)
    operator = make_user("22222222")
    m = _add(farm_id, operator, FarmRole.OPERADOR)
    response = login(admin).delete(
        f"/api/v1/farms/{farm_id}/members/{m.pk}/",
        {"reason": "incumplimiento"},
        format="json",
    )
    assert response.status_code == 204
    m.refresh_from_db()
    assert m.status == MembershipStatus.REVOKED


def test_revoke_self_forbidden(login, make_user):
    admin, farm_id, owner = _farm(make_user)
    other = make_user("22222222")
    _add(farm_id, other, FarmRole.ADMINISTRADOR)
    response = login(admin).delete(f"/api/v1/farms/{farm_id}/members/{owner.pk}/")
    assert response.status_code == 400


def test_revoke_records_audit(login, make_user):
    admin, farm_id, _ = _farm(make_user)
    operator = make_user("22222222")
    m = _add(farm_id, operator, FarmRole.OPERADOR)
    login(admin).delete(
        f"/api/v1/farms/{farm_id}/members/{m.pk}/",
        {"reason": "x"},
        format="json",
    )
    assert AuditEvent.objects.filter(
        farm_id=farm_id, event_type=EventType.MEMBER_REVOKED
    ).exists()


# --- Abandonar finca ---


def test_leave_success(login, make_user):
    admin, farm_id, _ = _farm(make_user)
    operator = make_user("22222222")
    m = _add(farm_id, operator, FarmRole.OPERADOR)
    response = login(operator).post(f"/api/v1/farms/{farm_id}/leave/")
    assert response.status_code == 204
    m.refresh_from_db()
    assert m.status == MembershipStatus.LEFT


def test_leave_last_admin_conflict(login, make_user):
    admin, farm_id, _ = _farm(make_user)
    response = login(admin).post(f"/api/v1/farms/{farm_id}/leave/")
    assert response.status_code == 409


def test_leave_foreign_user_not_found(login, make_user):
    admin, farm_id, _ = _farm(make_user)
    outsider = make_user("99999999")
    response = login(outsider).post(f"/api/v1/farms/{farm_id}/leave/")
    assert response.status_code == 404


# --- Transferencia ---


def test_transfer_success(login, make_user):
    admin, farm_id, owner = _farm(make_user)
    operator = make_user("22222222")
    m = _add(farm_id, operator, FarmRole.OPERADOR)
    response = login(admin).post(f"/api/v1/farms/{farm_id}/members/{m.pk}/transfer/")
    assert response.status_code == 200
    assert response.data["new_admin"]["id"] == m.pk
    assert response.data["new_admin"]["role"] == "administrador"
    assert response.data["previous_admin"]["id"] == owner.pk
    assert response.data["previous_admin"]["role"] == "operador"


def test_transfer_to_self_fails(login, make_user):
    admin, farm_id, owner = _farm(make_user)
    response = login(admin).post(f"/api/v1/farms/{farm_id}/members/{owner.pk}/transfer/")
    assert response.status_code == 400


def test_transfer_to_other_admin_fails(login, make_user):
    admin, farm_id, _ = _farm(make_user)
    other = make_user("22222222")
    m = _add(farm_id, other, FarmRole.ADMINISTRADOR)
    response = login(admin).post(f"/api/v1/farms/{farm_id}/members/{m.pk}/transfer/")
    assert response.status_code == 400


def test_transfer_non_admin_forbidden(login, make_user):
    admin, farm_id, _ = _farm(make_user)
    operator = make_user("22222222")
    _add(farm_id, operator, FarmRole.OPERADOR)
    consulta = make_user("33333333")
    m = _add(farm_id, consulta, FarmRole.CONSULTA)
    response = login(operator).post(f"/api/v1/farms/{farm_id}/members/{m.pk}/transfer/")
    assert response.status_code == 403


# --- Finca inactiva ---


def test_management_blocked_on_inactive_farm(login, make_user):
    admin, farm_id, _ = _farm(make_user)
    operator = make_user("22222222")
    m = _add(farm_id, operator, FarmRole.OPERADOR)
    services.deactivate_farm(user=admin, farm_id=farm_id)
    client = login(admin)
    assert client.get(f"/api/v1/farms/{farm_id}/members/").status_code == 404
    assert client.patch(
        f"/api/v1/farms/{farm_id}/members/{m.pk}/",
        {"role": "consulta"},
        format="json",
    ).status_code == 404


# --- Historial de membresía ---


def test_member_history_admin_only(login, make_user):
    admin, farm_id, _ = _farm(make_user)
    operator = make_user("22222222")
    m = _add(farm_id, operator, FarmRole.OPERADOR)
    services.change_member_role(
        user=admin, farm_id=farm_id, membership_id=m.pk, role=FarmRole.CONSULTA
    )
    response = login(admin).get(f"/api/v1/farms/{farm_id}/members/{m.pk}/history/")
    assert response.status_code == 200
    assert response.data["count"] == 1

    response = login(operator).get(f"/api/v1/farms/{farm_id}/members/{m.pk}/history/")
    assert response.status_code == 403
