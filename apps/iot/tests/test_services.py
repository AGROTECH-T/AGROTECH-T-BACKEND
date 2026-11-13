# ruff: noqa: S101
"""Servicios de dominio IoT (IoT-02.2).

Propósito: alta, consulta, validación, desactivación en cascada, reactivación
controlada, historial, auditoría en la misma transacción y comportamiento
cuando el motor no es MariaDB.
Contexto: solo mediante el orquestador desechable.
"""
from types import SimpleNamespace

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import DataError, IntegrityError, connection, transaction

from apps.accounts.errors import AppError
from apps.farms.services import deactivate_farm
from apps.iot import db_guard
from apps.iot import services as iot
from apps.iot.identifiers import IdentifierKind, is_valid_identifier
from apps.iot.models import Coordinator, CoordinatorGateway, Gateway, Sensor
from apps.iot.services import _common
from apps.iot.services import sensors as sensors_service
from apps.trazabilidad.models import AuditEvent, EventType
from apps.trazabilidad.services import AUDIT_REASON_MAX_LENGTH

pytestmark = pytest.mark.django_db


def _setup(admin, farm_id):
    gateway = iot.create_gateway(user=admin, farm_id=farm_id, name="Gateway norte")
    coordinator = iot.create_coordinator(user=admin, farm_id=farm_id, name="Coordinador 1")
    return gateway, coordinator


def _events(event_type):
    return AuditEvent.objects.filter(event_type=event_type)


def _error(callable_, status):
    with pytest.raises(AppError) as excinfo:
        callable_()
    assert excinfo.value.status_code == status
    return excinfo.value


# --- Creación ---


def test_create_gateway_and_coordinator(admin, farm_id):
    gateway = iot.create_gateway(
        user=admin, farm_id=farm_id, name="  Gateway norte  ", location_label=" Galpón 1 "
    )
    coordinator = iot.create_coordinator(user=admin, farm_id=farm_id, name="Coordinador")
    assert is_valid_identifier(gateway.uid, IdentifierKind.GATEWAY)
    assert is_valid_identifier(coordinator.uid, IdentifierKind.COORDINATOR)
    assert gateway.name == "Gateway norte"
    assert gateway.location_label == "Galpón 1"
    assert coordinator.location_label == ""
    assert gateway.created_by == admin and gateway.is_active
    event = _events(EventType.IOT_GATEWAY_CREATED).get()
    assert event.farm_id == farm_id and event.actor == admin
    assert event.entity_type == "iot_gateway" and event.entity_id == gateway.uid
    assert event.after_data == {
        "uid": gateway.uid,
        "name": "Gateway norte",
        "location_label": "Galpón 1",
        "is_active": True,
    }
    assert _events(EventType.IOT_COORDINATOR_CREATED).count() == 1


@pytest.mark.parametrize("name", ["", "   ", None, 5, "x" * 101])
def test_create_rejects_invalid_name(admin, farm_id, name):
    _error(lambda: iot.create_gateway(user=admin, farm_id=farm_id, name=name), 400)
    assert not Gateway.objects.exists()


def test_create_rejects_long_location_label(admin, farm_id):
    _error(
        lambda: iot.create_coordinator(
            user=admin, farm_id=farm_id, name="C", location_label="x" * 101
        ),
        400,
    )


def test_create_sensor_with_catalog_metric(admin, farm_id):
    _gateway, coordinator = _setup(admin, farm_id)
    sensor = iot.create_sensor(
        user=admin,
        farm_id=farm_id,
        coordinator_uid=coordinator.uid.lower(),
        channel=1,
        metric="relative_humidity",
    )
    assert is_valid_identifier(sensor.uid, IdentifierKind.SENSOR)
    assert sensor.active_channel == 1 and sensor.farm_id == farm_id
    assert iot.sensor_unit(sensor) == "%"
    event = _events(EventType.IOT_SENSOR_CREATED).get()
    assert event.after_data["coordinator_uid"] == coordinator.uid
    assert event.after_data["metric"] == "relative_humidity"


@pytest.mark.parametrize("metric", ["pressure", "", None, "TEMPERATURE"])
def test_create_sensor_rejects_unknown_metric(admin, farm_id, metric):
    _gateway, coordinator = _setup(admin, farm_id)
    _error(
        lambda: iot.create_sensor(
            user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid, channel=1, metric=metric
        ),
        400,
    )


@pytest.mark.parametrize("channel", [-1, 65536, "1", 1.0, True, None])
def test_create_sensor_rejects_invalid_channel(admin, farm_id, channel):
    """Fuera del rango de la columna en MariaDB (0-65535) o de tipo no entero."""
    _gateway, coordinator = _setup(admin, farm_id)
    _error(
        lambda: iot.create_sensor(
            user=admin,
            farm_id=farm_id,
            coordinator_uid=coordinator.uid,
            channel=channel,
            metric="temperature",
        ),
        400,
    )
    assert not Sensor.objects.exists()


def test_channel_range_comes_from_mariadb_column_type():
    assert connection.vendor == "mysql" and connection.mysql_is_mariadb
    assert sensors_service.channel_range() == (0, 65535)
    assert sensors_service.channel_range() == connection.ops.integer_field_range(
        "PositiveSmallIntegerField"
    )
    assert not hasattr(sensors_service, "MAX_CHANNEL_VALUE")


def test_sensor_channel_accepts_full_column_range(admin, farm_id):
    """Sin máximo funcional inventado: se aceptan los extremos de la columna."""
    _gateway, coordinator = _setup(admin, farm_id)
    for channel in (0, 200, 32767, 32768, 65535):
        sensor = iot.create_sensor(
            user=admin,
            farm_id=farm_id,
            coordinator_uid=coordinator.uid,
            channel=channel,
            metric="temperature",
        )
        sensor.refresh_from_db()
        assert sensor.channel == sensor.active_channel == channel


def test_mariadb_column_rejects_values_beyond_its_type(admin, farm_id):
    """La columna smallint UNSIGNED rechaza 65536 aunque se salte el servicio."""
    _gateway, coordinator = _setup(admin, farm_id)
    with pytest.raises((DataError, IntegrityError)), transaction.atomic():
        Sensor.objects.create(
            uid="SN-ABCDEFGHJK",
            farm_id=farm_id,
            coordinator=coordinator,
            channel=65536,
            active_channel=65536,
            metric="temperature",
        )
    assert not Sensor.objects.exists()


def test_create_sensor_rejects_busy_channel(admin, farm_id):
    _gateway, coordinator = _setup(admin, farm_id)
    kwargs = {
        "user": admin,
        "farm_id": farm_id,
        "coordinator_uid": coordinator.uid,
        "channel": 3,
        "metric": "temperature",
    }
    iot.create_sensor(**kwargs)
    _error(lambda: iot.create_sensor(**kwargs), 409)
    assert Sensor.objects.count() == 1
    assert _events(EventType.IOT_SENSOR_CREATED).count() == 1


def test_create_sensor_requires_active_coordinator(admin, farm_id):
    _gateway, coordinator = _setup(admin, farm_id)
    iot.deactivate_coordinator(user=admin, farm_id=farm_id, uid=coordinator.uid)
    _error(
        lambda: iot.create_sensor(
            user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid, channel=1,
            metric="temperature",
        ),
        409,
    )


# --- Identificadores: colisión y reintento ---


def test_uid_collision_is_retried(admin, farm_id, monkeypatch):
    existing = iot.create_gateway(user=admin, farm_id=farm_id, name="A")
    values = iter([existing.uid, "GW-ABCDEFGHJK"])
    monkeypatch.setattr(_common, "generate_identifier", lambda kind: next(values))
    second = iot.create_gateway(user=admin, farm_id=farm_id, name="B")
    assert second.uid == "GW-ABCDEFGHJK"
    assert Gateway.objects.count() == 2


def test_uid_collision_gives_up_after_limit(admin, farm_id, monkeypatch):
    existing = iot.create_gateway(user=admin, farm_id=farm_id, name="A")
    calls = []

    def always_same(kind):
        calls.append(kind)
        return existing.uid

    monkeypatch.setattr(_common, "generate_identifier", always_same)
    _error(lambda: iot.create_gateway(user=admin, farm_id=farm_id, name="B"), 500)
    assert len(calls) == _common.MAX_GENERATION_ATTEMPTS
    assert Gateway.objects.count() == 1
    assert _events(EventType.IOT_GATEWAY_CREATED).count() == 1


# --- Consulta ---


def test_list_and_get(admin, farm_id):
    gateway, coordinator = _setup(admin, farm_id)
    assert list(iot.list_gateways(user=admin, farm_id=farm_id)) == [gateway]
    assert iot.get_coordinator(user=admin, farm_id=farm_id, uid=f" {coordinator.uid} ") == (
        coordinator
    )
    iot.deactivate_gateway(user=admin, farm_id=farm_id, uid=gateway.uid)
    assert list(iot.list_gateways(user=admin, farm_id=farm_id)) == []
    assert list(iot.list_gateways(user=admin, farm_id=farm_id, include_inactive=True)) == [
        Gateway.objects.get()
    ]


@pytest.mark.parametrize("uid", ["GW-0000000000", "nope", "", None, "CO-ABCDEFGHJK"])
def test_get_unknown_or_malformed_uid_is_not_found(admin, farm_id, uid):
    _error(lambda: iot.get_gateway(user=admin, farm_id=farm_id, uid=uid), 404)


# --- Autorizaciones ---


def test_authorize_several_gateways_and_revoke(admin, farm_id):
    gateway, coordinator = _setup(admin, farm_id)
    second = iot.create_gateway(user=admin, farm_id=farm_id, name="Gateway sur")
    for gw in (gateway, second):
        iot.authorize_gateway(
            user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid, gateway_uid=gw.uid
        )
    active = iot.list_coordinator_authorizations(
        user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid, include_revoked=False
    )
    assert {row.gateway_id for row in active} == {gateway.pk, second.pk}

    revoked = iot.revoke_gateway_authorization(
        user=admin,
        farm_id=farm_id,
        coordinator_uid=coordinator.uid,
        gateway_uid=gateway.uid,
        reason="  mantenimiento  ",
    )
    assert revoked.active_pair is None and revoked.revoked_by == admin
    assert revoked.revoked_at >= revoked.authorized_at
    event = _events(EventType.IOT_GATEWAY_AUTHORIZATION_REVOKED).get()
    assert event.reason == "mantenimiento"
    assert event.before_data["active"] is True and event.after_data["active"] is False
    assert _events(EventType.IOT_GATEWAY_AUTHORIZED).count() == 2


def test_duplicate_authorization_is_conflict(admin, farm_id):
    gateway, coordinator = _setup(admin, farm_id)
    kwargs = {
        "user": admin,
        "farm_id": farm_id,
        "coordinator_uid": coordinator.uid,
        "gateway_uid": gateway.uid,
    }
    iot.authorize_gateway(**kwargs)
    _error(lambda: iot.authorize_gateway(**kwargs), 409)
    assert CoordinatorGateway.objects.count() == 1
    assert _events(EventType.IOT_GATEWAY_AUTHORIZED).count() == 1


def test_reauthorization_keeps_history(admin, farm_id):
    gateway, coordinator = _setup(admin, farm_id)
    kwargs = {
        "user": admin,
        "farm_id": farm_id,
        "coordinator_uid": coordinator.uid,
        "gateway_uid": gateway.uid,
    }
    iot.authorize_gateway(**kwargs)
    iot.revoke_gateway_authorization(**kwargs)
    iot.authorize_gateway(**kwargs)
    history = list(
        iot.list_coordinator_authorizations(
            user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid
        )
    )
    assert len(history) == 2
    assert sorted(row.is_active for row in history) == [False, True]


def test_revoke_without_active_authorization_is_not_found(admin, farm_id):
    gateway, coordinator = _setup(admin, farm_id)
    _error(
        lambda: iot.revoke_gateway_authorization(
            user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid, gateway_uid=gateway.uid
        ),
        404,
    )


def test_authorization_requires_active_devices(admin, farm_id):
    gateway, coordinator = _setup(admin, farm_id)
    iot.deactivate_gateway(user=admin, farm_id=farm_id, uid=gateway.uid)
    _error(
        lambda: iot.authorize_gateway(
            user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid, gateway_uid=gateway.uid
        ),
        409,
    )


# --- Desactivación ---


def test_deactivate_gateway_revokes_its_authorizations(admin, farm_id):
    gateway, coordinator = _setup(admin, farm_id)
    other = iot.create_gateway(user=admin, farm_id=farm_id, name="Otro")
    for gw in (gateway, other):
        iot.authorize_gateway(
            user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid, gateway_uid=gw.uid
        )
    iot.deactivate_gateway(user=admin, farm_id=farm_id, uid=gateway.uid, reason="averiado")
    gateway.refresh_from_db()
    assert not gateway.is_active and gateway.deactivated_by == admin
    assert gateway.deactivated_at is not None
    assert not CoordinatorGateway.objects.filter(gateway=gateway, active_pair=1).exists()
    assert CoordinatorGateway.objects.filter(gateway=other, active_pair=1).exists()
    revoked = _events(EventType.IOT_GATEWAY_AUTHORIZATION_REVOKED).get()
    assert revoked.metadata == {"cause": "gateway_deactivated"}
    event = _events(EventType.IOT_GATEWAY_DEACTIVATED).get()
    assert event.reason == "averiado" and event.metadata == {"revoked_authorizations": 1}
    _error(lambda: iot.deactivate_gateway(user=admin, farm_id=farm_id, uid=gateway.uid), 409)


def test_deactivate_coordinator_cascades_to_sensors_and_authorizations(admin, farm_id):
    gateway, coordinator = _setup(admin, farm_id)
    iot.authorize_gateway(
        user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid, gateway_uid=gateway.uid
    )
    for channel, metric in ((1, "temperature"), (2, "relative_humidity")):
        iot.create_sensor(
            user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid, channel=channel,
            metric=metric,
        )
    iot.deactivate_coordinator(user=admin, farm_id=farm_id, uid=coordinator.uid)

    assert not Coordinator.objects.get(pk=coordinator.pk).is_active
    sensors = Sensor.objects.filter(coordinator=coordinator)
    assert all(not s.is_active and s.active_channel is None for s in sensors)
    assert all(s.deactivated_by == admin for s in sensors)
    assert not CoordinatorGateway.objects.filter(active_pair=1).exists()
    assert _events(EventType.IOT_SENSOR_DEACTIVATED).count() == 2
    assert {e.metadata["cause"] for e in _events(EventType.IOT_SENSOR_DEACTIVATED)} == {
        "coordinator_deactivated"
    }
    event = _events(EventType.IOT_COORDINATOR_DEACTIVATED).get()
    assert event.metadata == {"revoked_authorizations": 1, "deactivated_sensors": 2}
    assert Gateway.objects.get(pk=gateway.pk).is_active


def test_deactivate_sensor_frees_channel(admin, farm_id):
    _gateway, coordinator = _setup(admin, farm_id)
    kwargs = {"user": admin, "farm_id": farm_id, "coordinator_uid": coordinator.uid,
              "channel": 1, "metric": "temperature"}
    first = iot.create_sensor(**kwargs)
    iot.deactivate_sensor(user=admin, farm_id=farm_id, uid=first.uid)
    second = iot.create_sensor(**kwargs)
    assert second.active_channel == 1
    _error(lambda: iot.deactivate_sensor(user=admin, farm_id=farm_id, uid=first.uid), 409)


# --- Reactivación controlada ---


def test_reactivation_requires_reason(admin, farm_id):
    gateway, _coordinator = _setup(admin, farm_id)
    iot.deactivate_gateway(user=admin, farm_id=farm_id, uid=gateway.uid)
    for reason in (None, "", "   "):
        _error(
            lambda reason=reason: iot.reactivate_gateway(
                user=admin, farm_id=farm_id, uid=gateway.uid, reason=reason
            ),
            400,
        )
    _error(
        lambda: iot.reactivate_gateway(
            user=admin, farm_id=farm_id, uid=gateway.uid, reason="x" * (AUDIT_REASON_MAX_LENGTH + 1)
        ),
        400,
    )
    assert not Gateway.objects.get(pk=gateway.pk).is_active


def test_reactivate_gateway_does_not_restore_authorizations(admin, farm_id):
    gateway, coordinator = _setup(admin, farm_id)
    iot.authorize_gateway(
        user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid, gateway_uid=gateway.uid
    )
    iot.deactivate_gateway(user=admin, farm_id=farm_id, uid=gateway.uid)
    iot.reactivate_gateway(user=admin, farm_id=farm_id, uid=gateway.uid, reason="reparado")
    gateway.refresh_from_db()
    assert gateway.is_active and gateway.deactivated_at is None and gateway.deactivated_by is None
    assert not CoordinatorGateway.objects.filter(active_pair=1).exists()
    event = _events(EventType.IOT_GATEWAY_REACTIVATED).get()
    assert event.reason == "reparado"
    assert event.metadata == {"restored_authorizations": 0, "restored_sensors": 0}
    _error(
        lambda: iot.reactivate_gateway(user=admin, farm_id=farm_id, uid=gateway.uid, reason="x"),
        409,
    )


def test_reactivate_coordinator_does_not_restore_sensors(admin, farm_id):
    _gateway, coordinator = _setup(admin, farm_id)
    sensor = iot.create_sensor(
        user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid, channel=1,
        metric="temperature",
    )
    iot.deactivate_coordinator(user=admin, farm_id=farm_id, uid=coordinator.uid)
    iot.reactivate_coordinator(user=admin, farm_id=farm_id, uid=coordinator.uid, reason="ok")
    assert Coordinator.objects.get(pk=coordinator.pk).is_active
    assert not Sensor.objects.get(pk=sensor.pk).is_active
    assert _events(EventType.IOT_COORDINATOR_REACTIVATED).count() == 1


def test_reactivate_sensor_requires_active_coordinator(admin, farm_id):
    _gateway, coordinator = _setup(admin, farm_id)
    sensor = iot.create_sensor(
        user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid, channel=1,
        metric="temperature",
    )
    iot.deactivate_coordinator(user=admin, farm_id=farm_id, uid=coordinator.uid)
    _error(
        lambda: iot.reactivate_sensor(user=admin, farm_id=farm_id, uid=sensor.uid, reason="x"),
        409,
    )
    iot.reactivate_coordinator(user=admin, farm_id=farm_id, uid=coordinator.uid, reason="ok")
    iot.reactivate_sensor(user=admin, farm_id=farm_id, uid=sensor.uid, reason="reconectado")
    sensor.refresh_from_db()
    assert sensor.is_active and sensor.active_channel == 1 and sensor.deactivated_at is None
    assert _events(EventType.IOT_SENSOR_REACTIVATED).get().reason == "reconectado"


def test_reactivate_sensor_requires_free_channel(admin, farm_id):
    _gateway, coordinator = _setup(admin, farm_id)
    kwargs = {"user": admin, "farm_id": farm_id, "coordinator_uid": coordinator.uid,
              "channel": 1, "metric": "temperature"}
    old = iot.create_sensor(**kwargs)
    iot.deactivate_sensor(user=admin, farm_id=farm_id, uid=old.uid)
    iot.create_sensor(**kwargs)
    _error(lambda: iot.reactivate_sensor(user=admin, farm_id=farm_id, uid=old.uid, reason="x"), 409)
    assert not Sensor.objects.get(pk=old.pk).is_active
    assert not _events(EventType.IOT_SENSOR_REACTIVATED).exists()


def test_reactivate_sensor_rejects_metric_outside_catalog(admin, farm_id, monkeypatch):
    _gateway, coordinator = _setup(admin, farm_id)
    sensor = iot.create_sensor(
        user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid, channel=1,
        metric="temperature",
    )
    iot.deactivate_sensor(user=admin, farm_id=farm_id, uid=sensor.uid)
    monkeypatch.setattr("apps.iot.services.sensors.is_known_metric", lambda code: False)
    _error(
        lambda: iot.reactivate_sensor(user=admin, farm_id=farm_id, uid=sensor.uid, reason="x"),
        409,
    )


# --- Auditoría: misma transacción y sin secretos ---


@pytest.mark.parametrize(
    ("target", "operation"),
    [
        ("apps.iot.services._common.record_audit_event", "create_gateway"),
        ("apps.iot.services._common.record_audit_event", "deactivate_coordinator"),
        ("apps.iot.services._common.record_audit_event", "authorize_gateway"),
    ],
)
def test_audit_failure_rolls_back_operation(admin, farm_id, monkeypatch, target, operation):
    gateway, coordinator = _setup(admin, farm_id)
    iot.create_sensor(
        user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid, channel=1,
        metric="temperature",
    )
    before = (
        Gateway.objects.count(),
        CoordinatorGateway.objects.count(),
        list(Sensor.objects.values_list("is_active", flat=True)),
        Coordinator.objects.get(pk=coordinator.pk).is_active,
        AuditEvent.objects.count(),
    )

    def boom(**kwargs):
        raise RuntimeError("auditoría caída")

    monkeypatch.setattr(target, boom)
    calls = {
        "create_gateway": lambda: iot.create_gateway(user=admin, farm_id=farm_id, name="X"),
        "deactivate_coordinator": lambda: iot.deactivate_coordinator(
            user=admin, farm_id=farm_id, uid=coordinator.uid
        ),
        "authorize_gateway": lambda: iot.authorize_gateway(
            user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid, gateway_uid=gateway.uid
        ),
    }
    with pytest.raises(RuntimeError):
        calls[operation]()
    after = (
        Gateway.objects.count(),
        CoordinatorGateway.objects.count(),
        list(Sensor.objects.values_list("is_active", flat=True)),
        Coordinator.objects.get(pk=coordinator.pk).is_active,
        AuditEvent.objects.count(),
    )
    assert after == before


def test_audit_payloads_contain_no_sensitive_keys(admin, farm_id):
    gateway, coordinator = _setup(admin, farm_id)
    iot.authorize_gateway(
        user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid, gateway_uid=gateway.uid
    )
    iot.create_sensor(
        user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid, channel=1,
        metric="temperature",
    )
    iot.deactivate_coordinator(user=admin, farm_id=farm_id, uid=coordinator.uid)
    forbidden = {"key", "hmac", "secret", "password", "token", "credential"}
    for event in AuditEvent.objects.filter(event_type__startswith="iot_"):
        for payload in (event.before_data or {}, event.after_data or {}, event.metadata or {}):
            for key in payload:
                assert not any(word in key.lower() for word in forbidden), key


# --- Motor distinto de MariaDB ---


@pytest.mark.parametrize("vendor", ["postgresql", "sqlite"])
def test_writes_fail_closed_on_other_engines(admin, farm_id, monkeypatch, vendor):
    gateway, coordinator = _setup(admin, farm_id)
    events_before = AuditEvent.objects.count()
    monkeypatch.setattr(
        db_guard, "connections", {"default": SimpleNamespace(vendor=vendor)}
    )
    operations = [
        lambda: iot.create_gateway(user=admin, farm_id=farm_id, name="X"),
        lambda: iot.create_coordinator(user=admin, farm_id=farm_id, name="X"),
        lambda: iot.create_sensor(
            user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid, channel=1,
            metric="temperature",
        ),
        lambda: iot.authorize_gateway(
            user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid, gateway_uid=gateway.uid
        ),
        lambda: iot.deactivate_gateway(user=admin, farm_id=farm_id, uid=gateway.uid),
        lambda: iot.deactivate_coordinator(user=admin, farm_id=farm_id, uid=coordinator.uid),
        lambda: iot.reactivate_gateway(user=admin, farm_id=farm_id, uid=gateway.uid, reason="x"),
    ]
    for operation in operations:
        with pytest.raises(ImproperlyConfigured):
            operation()
    assert Gateway.objects.count() == 1 and Coordinator.objects.count() == 1
    assert AuditEvent.objects.count() == events_before


def test_reads_do_not_require_mariadb_guard(admin, farm_id, monkeypatch):
    gateway, _coordinator = _setup(admin, farm_id)
    monkeypatch.setattr(
        db_guard, "connections", {"default": SimpleNamespace(vendor="postgresql")}
    )
    assert iot.get_gateway(user=admin, farm_id=farm_id, uid=gateway.uid) == gateway


def test_inactive_farm_blocks_reads_and_writes(admin, farm_id):
    gateway, _coordinator = _setup(admin, farm_id)
    deactivate_farm(user=admin, farm_id=farm_id)
    _error(lambda: iot.get_gateway(user=admin, farm_id=farm_id, uid=gateway.uid), 404)
    _error(lambda: iot.create_gateway(user=admin, farm_id=farm_id, name="X"), 404)
