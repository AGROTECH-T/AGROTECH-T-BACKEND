# ruff: noqa: S101
"""Restricciones de base de datos de los modelos IoT (IoT-02.2).

Propósito: comprobar en MariaDB, escribiendo por ORM sin pasar por los
servicios, que la integridad no depende de validaciones en Python: formato y
unicidad de ``uid``, métricas, canal activo, autorización activa, coherencia de
estados, FK compuestas entre fincas y PROTECT.
Contexto: solo mediante el orquestador desechable.
"""
from datetime import timedelta

import pytest
from django.db import IntegrityError, connection, transaction
from django.db.models import ProtectedError
from django.utils import timezone

from apps.farms.models import Farm
from apps.iot.catalog import MetricCode, metric_unit
from apps.iot.identifiers import IdentifierKind, generate_identifier
from apps.iot.models import ACTIVE_PAIR, Coordinator, CoordinatorGateway, Gateway, Sensor

pytestmark = pytest.mark.django_db


def _gateway(farm_id, **kwargs):
    kwargs.setdefault("uid", generate_identifier(IdentifierKind.GATEWAY))
    return Gateway.objects.create(farm_id=farm_id, name=kwargs.pop("name", "GW"), **kwargs)


def _coordinator(farm_id, **kwargs):
    kwargs.setdefault("uid", generate_identifier(IdentifierKind.COORDINATOR))
    return Coordinator.objects.create(farm_id=farm_id, name=kwargs.pop("name", "CO"), **kwargs)


def _sensor(coordinator, channel=1, **kwargs):
    kwargs.setdefault("uid", generate_identifier(IdentifierKind.SENSOR))
    kwargs.setdefault("farm_id", coordinator.farm_id)
    kwargs.setdefault("metric", MetricCode.TEMPERATURE)
    kwargs.setdefault("active_channel", channel)
    return Sensor.objects.create(coordinator=coordinator, channel=channel, **kwargs)


def _authorization(coordinator, gateway, **kwargs):
    kwargs.setdefault("farm_id", coordinator.farm_id)
    kwargs.setdefault("authorized_at", timezone.now())
    return CoordinatorGateway.objects.create(coordinator=coordinator, gateway=gateway, **kwargs)


def _rejected(callable_):
    with pytest.raises(IntegrityError), transaction.atomic():
        callable_()


def test_engine_is_mariadb_11_8():
    assert connection.vendor == "mysql"
    assert connection.mysql_is_mariadb is True
    assert connection.mysql_version[:2] == (11, 8)


def test_four_models_are_created(farm_id):
    gateway = _gateway(farm_id)
    coordinator = _coordinator(farm_id)
    sensor = _sensor(coordinator)
    authorization = _authorization(coordinator, gateway)
    assert gateway.pk and coordinator.pk and sensor.pk and authorization.pk
    assert authorization.is_active is True
    assert metric_unit(sensor.metric) == "°C"


# --- Identificadores ---


@pytest.mark.parametrize(
    "bad_uid",
    [
        "CO-7K3M9QXT2A",  # prefijo de otro tipo
        "GW-7K3M9QXT2",  # corto
        "gw-7k3m9qxt2a",  # minúsculas
        "GW-7K3M9QXT0A",  # carácter ambiguo
        "GW_7K3M9QXT2A",
    ],
)
def test_gateway_uid_format_is_checked_by_database(farm_id, bad_uid):
    _rejected(lambda: _gateway(farm_id, uid=bad_uid))


def test_coordinator_and_sensor_uid_formats(farm_id):
    _rejected(lambda: _coordinator(farm_id, uid="GW-7K3M9QXT2A"))
    coordinator = _coordinator(farm_id)
    _rejected(lambda: _sensor(coordinator, uid="CO-7K3M9QXT2A"))


def test_uid_is_globally_unique(farm_id, other_farm_id):
    gateway = _gateway(farm_id)
    _rejected(lambda: _gateway(other_farm_id, uid=gateway.uid))


# --- Sensores ---


def test_metric_is_restricted_to_catalog(farm_id):
    coordinator = _coordinator(farm_id)
    _rejected(lambda: _sensor(coordinator, metric="pressure"))


def test_single_active_sensor_per_channel(farm_id):
    coordinator = _coordinator(farm_id)
    _sensor(coordinator, channel=1)
    _rejected(lambda: _sensor(coordinator, channel=1))


def test_inactive_sensors_keep_history_on_same_channel(farm_id):
    coordinator = _coordinator(farm_id)
    now = timezone.now()
    for _ in range(2):
        _sensor(
            coordinator,
            channel=1,
            active_channel=None,
            is_active=False,
            deactivated_at=now,
        )
    _sensor(coordinator, channel=1)
    assert Sensor.objects.filter(coordinator=coordinator, channel=1).count() == 3


def test_same_channel_on_other_coordinator_is_allowed(farm_id):
    _sensor(_coordinator(farm_id), channel=1)
    _sensor(_coordinator(farm_id), channel=1)


def test_active_channel_must_match_state(farm_id):
    coordinator = _coordinator(farm_id)
    _rejected(lambda: _sensor(coordinator, channel=1, active_channel=None))
    _rejected(lambda: _sensor(coordinator, channel=1, active_channel=2))
    _rejected(
        lambda: _sensor(
            coordinator,
            channel=1,
            active_channel=1,
            is_active=False,
            deactivated_at=timezone.now(),
        )
    )


def test_lifecycle_coherence(farm_id, admin):
    _rejected(lambda: _gateway(farm_id, is_active=False))
    _rejected(lambda: _gateway(farm_id, deactivated_at=timezone.now()))
    _rejected(lambda: _coordinator(farm_id, deactivated_by=admin))
    _gateway(farm_id, is_active=False, deactivated_at=timezone.now())


# --- Autorizaciones ---


def test_single_active_authorization_per_pair(farm_id):
    coordinator, gateway = _coordinator(farm_id), _gateway(farm_id)
    _authorization(coordinator, gateway)
    _rejected(lambda: _authorization(coordinator, gateway))


def test_revoked_authorizations_are_kept(farm_id):
    coordinator, gateway = _coordinator(farm_id), _gateway(farm_id)
    now = timezone.now()
    for _ in range(2):
        _authorization(coordinator, gateway, authorized_at=now, active_pair=None, revoked_at=now)
    _authorization(coordinator, gateway)
    assert CoordinatorGateway.objects.filter(coordinator=coordinator).count() == 3


def test_coordinator_may_have_several_gateways(farm_id):
    coordinator = _coordinator(farm_id)
    _authorization(coordinator, _gateway(farm_id))
    _authorization(coordinator, _gateway(farm_id))
    assert coordinator.gateway_authorizations.filter(active_pair=ACTIVE_PAIR).count() == 2


def test_authorization_state_coherence(farm_id, admin):
    coordinator, gateway = _coordinator(farm_id), _gateway(farm_id)
    now = timezone.now()
    _rejected(lambda: _authorization(coordinator, gateway, revoked_at=now))
    _rejected(lambda: _authorization(coordinator, gateway, active_pair=None))
    _rejected(lambda: _authorization(coordinator, gateway, revoked_by=admin))
    _rejected(lambda: _authorization(coordinator, gateway, active_pair=2))
    _rejected(
        lambda: _authorization(
            coordinator,
            gateway,
            authorized_at=now,
            revoked_at=now - timedelta(seconds=1),
            active_pair=None,
        )
    )


# --- Integridad entre fincas (FK compuestas) ---


def test_authorization_cannot_mix_farms(farm_id, other_farm_id):
    coordinator = _coordinator(farm_id)
    foreign_gateway = _gateway(other_farm_id)
    _rejected(lambda: _authorization(coordinator, foreign_gateway, farm_id=farm_id))
    _rejected(lambda: _authorization(coordinator, foreign_gateway, farm_id=other_farm_id))


def test_sensor_farm_must_match_coordinator(farm_id, other_farm_id):
    coordinator = _coordinator(farm_id)
    _rejected(lambda: _sensor(coordinator, farm_id=other_farm_id))


def test_device_farm_cannot_change_while_referenced(farm_id, other_farm_id):
    coordinator, gateway = _coordinator(farm_id), _gateway(farm_id)
    _authorization(coordinator, gateway)
    _sensor(coordinator)
    _rejected(lambda: Gateway.objects.filter(pk=gateway.pk).update(farm_id=other_farm_id))
    _rejected(
        lambda: Coordinator.objects.filter(pk=coordinator.pk).update(farm_id=other_farm_id)
    )


def test_composite_foreign_keys_exist():
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT CONSTRAINT_NAME, GROUP_CONCAT(COLUMN_NAME ORDER BY ORDINAL_POSITION) "
            "FROM information_schema.KEY_COLUMN_USAGE "
            "WHERE TABLE_SCHEMA = DATABASE() AND CONSTRAINT_NAME LIKE %s "
            "GROUP BY CONSTRAINT_NAME",
            ["iot\\_%\\_farm\\_fk"],
        )
        found = dict(cursor.fetchall())
    assert found == {
        "iot_sensor_coord_farm_fk": "coordinator_id,farm_id",
        "iot_cg_coord_farm_fk": "coordinator_id,farm_id",
        "iot_cg_gw_farm_fk": "gateway_id,farm_id",
    }


# --- PROTECT ---


def test_farm_with_devices_cannot_be_deleted(farm_id):
    _gateway(farm_id)
    with pytest.raises(ProtectedError):
        Farm.objects.get(pk=farm_id).delete()


def test_referenced_devices_cannot_be_deleted(farm_id):
    coordinator, gateway = _coordinator(farm_id), _gateway(farm_id)
    _sensor(coordinator)
    _authorization(coordinator, gateway)
    with pytest.raises(ProtectedError):
        coordinator.delete()
    with pytest.raises(ProtectedError):
        gateway.delete()
