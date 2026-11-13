"""Pruebas del endpoint de historial de auditoría de Farms."""
import pytest

from apps.farms import services
from apps.farms.models import Farm, FarmMembership, FarmRole
from apps.trazabilidad.models import AuditEvent, EventType

pytestmark = pytest.mark.django_db


def _admin_with_farm(make_user, name="La Esperanza"):
    admin = make_user("11111111")
    membership = services.create_farm(user=admin, name=name)
    return admin, membership


def _direct_farm_with_admin(make_user, name="La Esperanza"):
    """Crea finca y membresía sin pasar por el servicio (sin eventos)."""
    admin = make_user("11111111")
    farm = Farm.objects.create(name=name)
    FarmMembership.objects.create(farm=farm, user=admin, role=FarmRole.ADMINISTRADOR)
    return admin, farm


def _seed_events(farm, n):
    for i in range(n):
        AuditEvent.objects.create(
            event_type=EventType.FARM_UPDATED,
            farm=farm,
            farm_identifier=farm.pk,
            entity_type="farm",
            entity_id=str(farm.pk),
            after_data={"name": f"N{i}"},
        )


# --- Autorización ---


def test_admin_can_read_active_farm_history(login, make_user):
    admin, membership = _admin_with_farm(make_user)
    response = login(admin).get(f"/api/v1/farms/{membership.farm_id}/history/")
    assert response.status_code == 200
    assert response.data["count"] == 1
    assert response.data["results"][0]["event_type"] == "farm_created"


def test_admin_can_read_deactivated_farm_history(login, make_user):
    admin, membership = _admin_with_farm(make_user)
    services.deactivate_farm(user=admin, farm_id=membership.farm_id)
    response = login(admin).get(f"/api/v1/farms/{membership.farm_id}/history/")
    assert response.status_code == 200
    types = [event["event_type"] for event in response.data["results"]]
    assert "farm_deactivated" in types
    assert "farm_created" in types


def test_operator_gets_404(login, make_user):
    admin, membership = _admin_with_farm(make_user)
    operator = make_user("22222222")
    FarmMembership.objects.create(
        farm_id=membership.farm_id,
        user=operator,
        role=FarmRole.OPERADOR,
    )
    response = login(operator).get(f"/api/v1/farms/{membership.farm_id}/history/")
    assert response.status_code == 404


def test_consulta_gets_404(login, make_user):
    admin, membership = _admin_with_farm(make_user)
    consulta = make_user("33333333")
    FarmMembership.objects.create(
        farm_id=membership.farm_id,
        user=consulta,
        role=FarmRole.CONSULTA,
    )
    response = login(consulta).get(f"/api/v1/farms/{membership.farm_id}/history/")
    assert response.status_code == 404


def test_revoked_member_gets_404(login, make_user):
    admin, membership = _admin_with_farm(make_user)
    member = make_user("44444444")
    FarmMembership.objects.create(
        farm_id=membership.farm_id,
        user=member,
        role=FarmRole.CONSULTA,
    )
    FarmMembership.objects.filter(farm_id=membership.farm_id, user=member).delete()
    response = login(member).get(f"/api/v1/farms/{membership.farm_id}/history/")
    assert response.status_code == 404


def test_external_user_gets_404(login, make_user):
    admin, membership = _admin_with_farm(make_user)
    outsider = make_user("55555555")
    response = login(outsider).get(f"/api/v1/farms/{membership.farm_id}/history/")
    assert response.status_code == 404


def test_missing_farm_gets_404(login, make_user):
    admin = make_user("11111111")
    response = login(admin).get("/api/v1/farms/999999/history/")
    assert response.status_code == 404


# --- Aislamiento ---


def test_admin_cannot_read_other_farm_events(login, make_user):
    admin_a = make_user("11111111")
    services.create_farm(user=admin_a, name="A")
    admin_b = make_user("22222222")
    farm_b = services.create_farm(user=admin_b, name="B").farm_id
    response = login(admin_a).get(f"/api/v1/farms/{farm_b}/history/")
    assert response.status_code == 404


def test_history_excludes_unscoped_events(login, make_user):
    admin, membership = _admin_with_farm(make_user)
    AuditEvent.objects.create(
        event_type=EventType.FARM_CREATED,
        entity_type="farm",
        entity_id="999",
        farm=None,
    )
    response = login(admin).get(f"/api/v1/farms/{membership.farm_id}/history/")
    assert response.status_code == 200
    assert response.data["count"] == 1


# --- Paginación ---


def test_history_default_page_size_20(login, make_user):
    admin, farm = _direct_farm_with_admin(make_user)
    _seed_events(farm, 25)
    response = login(admin).get(f"/api/v1/farms/{farm.pk}/history/")
    assert response.status_code == 200
    assert response.data["count"] == 25
    assert len(response.data["results"]) == 20
    assert response.data["next"] is not None
    assert response.data["previous"] is None


def test_history_second_page(login, make_user):
    admin, farm = _direct_farm_with_admin(make_user)
    _seed_events(farm, 25)
    response = login(admin).get(f"/api/v1/farms/{farm.pk}/history/?page=2")
    assert response.status_code == 200
    assert len(response.data["results"]) == 5
    assert response.data["previous"] is not None
    assert response.data["next"] is None


def test_history_page_size_override(login, make_user):
    admin, farm = _direct_farm_with_admin(make_user)
    _seed_events(farm, 3)
    response = login(admin).get(f"/api/v1/farms/{farm.pk}/history/?page_size=100")
    assert response.status_code == 200
    assert len(response.data["results"]) == 3


def test_history_page_size_capped_at_100(login, make_user):
    admin, farm = _direct_farm_with_admin(make_user)
    _seed_events(farm, 105)
    response = login(admin).get(f"/api/v1/farms/{farm.pk}/history/?page_size=1000")
    assert response.status_code == 200
    assert len(response.data["results"]) == 100


def test_history_order_desc_by_created_and_id(login, make_user):
    admin, membership = _admin_with_farm(make_user)
    services.update_farm(user=admin, farm_id=membership.farm_id, name="N1")
    services.update_farm(user=admin, farm_id=membership.farm_id, name="N2")
    response = login(admin).get(f"/api/v1/farms/{membership.farm_id}/history/")
    types = [event["event_type"] for event in response.data["results"]]
    assert types[0] == "farm_updated"
    assert types[-1] == "farm_created"


def test_empty_history(login, make_user):
    admin, farm = _direct_farm_with_admin(make_user)
    response = login(admin).get(f"/api/v1/farms/{farm.pk}/history/")
    assert response.status_code == 200
    assert response.data["count"] == 0
    assert response.data["results"] == []


# --- Privacidad ---


def test_history_response_excludes_internal_fields(login, make_user):
    admin, membership = _admin_with_farm(make_user)
    services.deactivate_farm(user=admin, farm_id=membership.farm_id)
    response = login(admin).get(f"/api/v1/farms/{membership.farm_id}/history/")
    result = response.data["results"][0]
    assert "event_type" in result
    assert "event_type_label" in result
    assert "actor_display" in result
    assert "metadata" not in result
    assert "actor_identifier" not in result
    assert "farm_identifier" not in result
    assert "actor" not in result
    assert "farm" not in result


# --- Sin efectos secundarios ---


def test_history_does_not_reactivate_or_mutate(login, make_user):
    admin, membership = _admin_with_farm(make_user)
    services.deactivate_farm(user=admin, farm_id=membership.farm_id)
    farm = Farm.objects.get(pk=membership.farm_id)
    deactivated_at_before = farm.deactivated_at
    events_before = AuditEvent.objects.filter(farm_id=membership.farm_id).count()

    response = login(admin).get(f"/api/v1/farms/{membership.farm_id}/history/")
    assert response.status_code == 200

    farm.refresh_from_db()
    assert farm.is_active is False
    assert farm.deactivated_at == deactivated_at_before
    assert AuditEvent.objects.filter(farm_id=membership.farm_id).count() == events_before
