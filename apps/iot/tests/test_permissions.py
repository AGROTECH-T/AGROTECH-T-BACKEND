# ruff: noqa: S101
"""Permisos y aislamiento entre fincas en IoT (IoT-02.2).

Propósito: solo el administrador escribe; todo integrante activo lee; las
capacidades ``can_read_iot`` y ``can_manage_iot`` coinciden con lo que permiten
los servicios; los recursos de otra finca responden 404 sin revelar que
existen.
Contexto: solo mediante el orquestador desechable.
"""
from types import SimpleNamespace

import pytest
from django.core.exceptions import ImproperlyConfigured

from apps.accounts.errors import AppError
from apps.farms.capabilities import IOT_MANAGE_ROLES, farm_capabilities
from apps.farms.models import FarmMembership, FarmRole
from apps.iot import db_guard
from apps.iot import services as iot
from apps.iot.services import access
from apps.iot.services.access import IOT_MANAGE_FORBIDDEN
from apps.permissions.catalog import PERMISSION_CATALOG
from apps.iot.tests.conftest import make_source_uid

pytestmark = pytest.mark.django_db


def _error(callable_, status):
    with pytest.raises(AppError) as excinfo:
        callable_()
    assert excinfo.value.status_code == status
    return excinfo.value


@pytest.fixture
def devices(admin, farm_id):
    gateway = iot.create_gateway(user=admin, farm_id=farm_id, name="G")
    coordinator = iot.create_coordinator(user=admin, farm_id=farm_id, name="C")
    source_uid = make_source_uid(user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid)
    sensor = iot.create_sensor(
        user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid,
        source_uid=source_uid, channel=1,
        metric="temperature",
    )
    return gateway, coordinator, sensor


def _writes(user, farm_id, gateway, coordinator, sensor):
    return [
        lambda: iot.create_gateway(user=user, farm_id=farm_id, name="X"),
        lambda: iot.create_coordinator(user=user, farm_id=farm_id, name="X"),
        lambda: iot.create_sensor(
            user=user, farm_id=farm_id, coordinator_uid=coordinator.uid,
            source_uid=sensor.source.uid, channel=9,
            metric="temperature",
        ),
        lambda: iot.authorize_gateway(
            user=user, farm_id=farm_id, coordinator_uid=coordinator.uid, gateway_uid=gateway.uid
        ),
        lambda: iot.revoke_gateway_authorization(
            user=user, farm_id=farm_id, coordinator_uid=coordinator.uid, gateway_uid=gateway.uid
        ),
        lambda: iot.deactivate_gateway(user=user, farm_id=farm_id, uid=gateway.uid),
        lambda: iot.deactivate_coordinator(user=user, farm_id=farm_id, uid=coordinator.uid),
        lambda: iot.deactivate_sensor(user=user, farm_id=farm_id, uid=sensor.uid),
        lambda: iot.reactivate_gateway(user=user, farm_id=farm_id, uid=gateway.uid, reason="x"),
        lambda: iot.reactivate_coordinator(
            user=user, farm_id=farm_id, uid=coordinator.uid, reason="x"
        ),
        lambda: iot.reactivate_sensor(user=user, farm_id=farm_id, uid=sensor.uid, reason="x"),
    ]


def _reads(user, farm_id, gateway, coordinator, sensor):
    return [
        lambda: list(iot.list_gateways(user=user, farm_id=farm_id)),
        lambda: iot.get_gateway(user=user, farm_id=farm_id, uid=gateway.uid),
        lambda: list(iot.list_coordinators(user=user, farm_id=farm_id)),
        lambda: iot.get_coordinator(user=user, farm_id=farm_id, uid=coordinator.uid),
        lambda: list(iot.list_sensors(user=user, farm_id=farm_id)),
        lambda: iot.get_sensor(user=user, farm_id=farm_id, uid=sensor.uid),
        lambda: list(
            iot.list_coordinator_authorizations(
                user=user, farm_id=farm_id, coordinator_uid=coordinator.uid
            )
        ),
    ]


@pytest.mark.parametrize("role_fixture", ["operator", "viewer"])
def test_non_admin_members_cannot_write(request, farm_id, devices, role_fixture):
    member = request.getfixturevalue(role_fixture)
    for operation in _writes(member, farm_id, *devices):
        error = _error(operation, 403)
        assert error.message == IOT_MANAGE_FORBIDDEN


@pytest.mark.parametrize("role_fixture", ["admin", "operator", "viewer"])
def test_active_members_can_read(request, farm_id, devices, role_fixture):
    member = request.getfixturevalue(role_fixture)
    for operation in _reads(member, farm_id, *devices):
        operation()


def test_revoked_member_cannot_read_or_write(farm_id, devices, operator):
    FarmMembership.objects.filter(user=operator).update(status="revoked")
    for operation in _reads(operator, farm_id, *devices) + _writes(operator, farm_id, *devices):
        _error(operation, 404)


def test_inactive_admin_account_cannot_write(admin, farm_id, devices):
    admin.is_active = False
    admin.save(update_fields=["is_active"])
    _error(lambda: iot.create_gateway(user=admin, farm_id=farm_id, name="X"), 403)


def test_foreign_farm_resources_are_not_found(farm_id, devices, other_admin, other_farm_id):
    gateway, coordinator, sensor = devices
    # Un administrador de otra finca no puede ver ni tocar la finca principal.
    for operation in _reads(other_admin, farm_id, *devices) + _writes(
        other_admin, farm_id, *devices
    ):
        _error(operation, 404)
    # Usando su propia finca, los dispositivos ajenos no existen para él.
    for operation in _reads(other_admin, other_farm_id, *devices)[1::2] + _writes(
        other_admin, other_farm_id, *devices
    )[2:]:
        _error(operation, 404)


def test_cannot_authorize_gateway_of_another_farm(admin, farm_id, devices, other_admin,
                                                  other_farm_id):
    _gateway, coordinator, _sensor = devices
    foreign = iot.create_gateway(user=other_admin, farm_id=other_farm_id, name="Ajeno")
    error = _error(
        lambda: iot.authorize_gateway(
            user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid, gateway_uid=foreign.uid
        ),
        404,
    )
    assert error.message == "gateway no encontrado"


def test_listings_are_scoped_to_farm(admin, farm_id, devices, other_admin, other_farm_id):
    iot.create_gateway(user=other_admin, farm_id=other_farm_id, name="Ajeno")
    assert {g.farm_id for g in iot.list_gateways(user=admin, farm_id=farm_id)} == {farm_id}
    assert {g.farm_id for g in iot.list_gateways(user=other_admin, farm_id=other_farm_id)} == {
        other_farm_id
    }


@pytest.mark.parametrize("role_fixture", ["admin", "operator", "viewer"])
def test_capabilities_match_services(request, farm_id, devices, role_fixture):
    member = request.getfixturevalue(role_fixture)
    membership = FarmMembership.objects.select_related("farm", "user").get(
        farm_id=farm_id, user=member
    )
    caps = farm_capabilities(membership)
    assert caps["can_read_iot"] is True
    assert caps["can_manage_iot"] is (membership.role == FarmRole.ADMINISTRADOR)
    try:
        iot.create_gateway(user=member, farm_id=farm_id, name="X")
        allowed = True
    except AppError:
        allowed = False
    assert allowed is caps["can_manage_iot"]


def test_manage_roles_have_single_source():
    assert access.IOT_MANAGE_ROLES is IOT_MANAGE_ROLES
    assert frozenset({FarmRole.ADMINISTRADOR}) == IOT_MANAGE_ROLES


def test_general_permission_catalog_has_no_iot_entries():
    assert not any(permission.module == "iot" for permission in PERMISSION_CATALOG)


# --- Precedencia: autorización antes que validación y que motor (IoT-02.2-R2) ---


def _invalid_writes(user, farm_id, gateway, coordinator, sensor):
    """Escrituras con datos inválidos; cada una indica el error de validación."""
    too_long = "x" * 201
    return [
        (lambda: iot.create_gateway(user=user, farm_id=farm_id, name=""), 400),
        (lambda: iot.create_coordinator(user=user, farm_id=farm_id, name="C",
                                        location_label="x" * 101), 400),
        (lambda: iot.create_sensor(user=user, farm_id=farm_id, coordinator_uid=coordinator.uid,
                                   source_uid=sensor.source.uid, channel=-1, metric="temperature"), 400),
        (lambda: iot.create_sensor(user=user, farm_id=farm_id, coordinator_uid=coordinator.uid,
                                   source_uid=sensor.source.uid, channel=2, metric="pressure"), 400),
        (lambda: iot.create_sensor(user=user, farm_id=farm_id, coordinator_uid="malformado",
                                   source_uid=sensor.source.uid, channel=2, metric="temperature"), 404),
        (lambda: iot.authorize_gateway(user=user, farm_id=farm_id, coordinator_uid="x",
                                       gateway_uid=gateway.uid), 404),
        (lambda: iot.revoke_gateway_authorization(
            user=user, farm_id=farm_id, coordinator_uid=coordinator.uid,
            gateway_uid=gateway.uid, reason=too_long), 400),
        (lambda: iot.deactivate_gateway(user=user, farm_id=farm_id, uid="GW-0"), 404),
        (lambda: iot.deactivate_coordinator(user=user, farm_id=farm_id, uid=coordinator.uid,
                                            reason=too_long), 400),
        (lambda: iot.deactivate_sensor(user=user, farm_id=farm_id, uid=sensor.uid,
                                       reason=too_long), 400),
        (lambda: iot.reactivate_gateway(user=user, farm_id=farm_id, uid=gateway.uid,
                                        reason=""), 400),
        (lambda: iot.reactivate_coordinator(user=user, farm_id=farm_id, uid=coordinator.uid,
                                            reason=None), 400),
        (lambda: iot.reactivate_sensor(user=user, farm_id=farm_id, uid=sensor.uid,
                                       reason="  "), 400),
    ]


def test_non_member_gets_not_found_even_with_invalid_data(farm_id, devices, other_admin):
    for operation, _status in _invalid_writes(other_admin, farm_id, *devices):
        error = _error(operation, 404)
        assert error.message == "finca no encontrada"


@pytest.mark.parametrize("role_fixture", ["operator", "viewer"])
def test_non_admin_gets_forbidden_before_validation(request, farm_id, devices, role_fixture):
    member = request.getfixturevalue(role_fixture)
    for operation, _status in _invalid_writes(member, farm_id, *devices):
        error = _error(operation, 403)
        assert error.message == IOT_MANAGE_FORBIDDEN


def test_admin_receives_validation_errors(admin, farm_id, devices):
    for operation, status in _invalid_writes(admin, farm_id, *devices):
        _error(operation, status)


@pytest.mark.parametrize("vendor", ["postgresql", "sqlite"])
def test_engine_requirement_is_not_revealed_before_authorization(
    monkeypatch, admin, farm_id, devices, operator, viewer, other_admin, vendor
):
    monkeypatch.setattr(db_guard, "connections", {"default": SimpleNamespace(vendor=vendor)})
    for operation in _writes(other_admin, farm_id, *devices):
        _error(operation, 404)
    for member in (operator, viewer):
        for operation in _writes(member, farm_id, *devices):
            _error(operation, 403)
    # Solo el administrador autorizado llega a la guarda de MariaDB.
    with pytest.raises(ImproperlyConfigured):
        iot.create_gateway(user=admin, farm_id=farm_id, name="X")
    # La guarda va antes que la validación: con datos inválidos tampoco se valida.
    with pytest.raises(ImproperlyConfigured):
        iot.create_gateway(user=admin, farm_id=farm_id, name="")


def test_valid_operations_still_work_for_admin(admin, farm_id, devices):
    gateway, coordinator, sensor = devices
    iot.authorize_gateway(
        user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid, gateway_uid=gateway.uid
    )
    iot.revoke_gateway_authorization(
        user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid, gateway_uid=gateway.uid
    )
    iot.deactivate_sensor(user=admin, farm_id=farm_id, uid=sensor.uid)
    iot.reactivate_sensor(user=admin, farm_id=farm_id, uid=sensor.uid, reason="ok")
    iot.deactivate_gateway(user=admin, farm_id=farm_id, uid=gateway.uid)
    iot.reactivate_gateway(user=admin, farm_id=farm_id, uid=gateway.uid, reason="ok")
