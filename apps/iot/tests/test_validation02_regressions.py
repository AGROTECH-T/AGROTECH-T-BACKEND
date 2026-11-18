# ruff: noqa: S101
"""VALIDATION-02: rollback barriers, forward re-application and append-only guard.

Solo contra MariaDB 11.8 desechable (perfil ``config.settings_test_disposable_iot``).
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.db.migrations.exceptions import IrreversibleError
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder

import apps.iot.services as iot
from apps.accounts.models import User
from apps.iot.migrations._ingestion_history_triggers import TRIGGERS as GUARD_TRIGGERS
from apps.iot.migrations._ingestion_history_triggers import (
    _expected_statement,
    _normalize_sql,
    _trigger_state,
)
from apps.iot.models import IotMqttDeliveryPauseEvent, SensorSourceChange
from apps.iot.services.mqtt_delivery_pauses import pause_mqtt_deliveries

pytestmark = pytest.mark.django_db(transaction=True)

IOT_LEAF = ("iot", "0021_broker_credential")
IOT_0015 = "0015_iot_delivery_identity_pauses"
IOT_0012 = ("iot", "0012_iot_history_triggers")
DURABLE_TABLES = {
    "iot_mqtt_deliveries",
    "iot_mqtt_delivery_events",
    "iot_mqtt_delivery_pauses",
    "iot_mqtt_delivery_pause_events",
}


def _executor() -> MigrationExecutor:
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    return executor


def _restore_leaves() -> None:
    executor = _executor()
    executor.migrate(executor.loader.graph.leaf_nodes())


def _tables() -> set[str]:
    with connection.cursor() as cursor:
        return set(connection.introspection.table_names(cursor))


def _applied_iot() -> set[str]:
    return {
        name
        for app, name in MigrationRecorder(connection).applied_migrations()
        if app == "iot"
    }


def _guards_installed() -> bool:
    for name, table, event, message in GUARD_TRIGGERS:
        found = _trigger_state(connection, name)
        if found is None:
            return False
        actual = (found[0], found[1], found[2], _normalize_sql(found[3]))
        assert actual == (table, "BEFORE", event, _expected_statement(message))
    return True


# --------------------------------------------------------------------------
# Fase B: barrera de reversión y reaplicación hacia adelante
# --------------------------------------------------------------------------


def test_default_rollback_is_refused_before_any_change(settings):
    settings.IOT_ALLOW_DESTRUCTIVE_HISTORY_ROLLBACK = False
    applied_before = _applied_iot()
    try:
        # FLESPI-01: the newest barrier is now the first reverse step of 0021
        # (previously 0020, BUILD-FLEPSI-02H); 0020/0019 keep theirs.
        with pytest.raises(IrreversibleError, match="iot.0021"):
            _executor().migrate([IOT_0012])

        # The 0021 barrier refuses before ANY change (nothing is unapplied);
        # 0015 and everything holding durable data stays applied and intact.
        assert _applied_iot() == applied_before
        assert IOT_0015 in _applied_iot()
        assert DURABLE_TABLES <= _tables()
        assert _guards_installed()
    finally:
        _restore_leaves()


def test_explicit_rollback_removes_guards_and_forward_restores_schema(settings):
    settings.IOT_ALLOW_DESTRUCTIVE_HISTORY_ROLLBACK = True
    try:
        _executor().migrate([IOT_0012])
        assert IOT_LEAF[1] not in _applied_iot()
        assert IOT_0015 not in _applied_iot()
        assert not DURABLE_TABLES & _tables()
        assert all(_trigger_state(connection, name) is None for name, *_ in GUARD_TRIGGERS)

        _executor().migrate([IOT_LEAF])
        assert IOT_LEAF[1] in _applied_iot()
        assert DURABLE_TABLES <= _tables()
        assert _guards_installed()
    finally:
        _restore_leaves()


def test_opt_in_is_not_enabled_by_project_settings():
    from config import settings as project_settings

    assert not hasattr(project_settings, "IOT_ALLOW_DESTRUCTIVE_HISTORY_ROLLBACK")


# --------------------------------------------------------------------------
# Fase D: guard append-only frente al Collector de Django
# --------------------------------------------------------------------------


def test_empty_append_only_update_is_a_noop_and_real_update_is_refused(db):
    empty = IotMqttDeliveryPauseEvent.objects.filter(pk__isnull=True)
    assert empty.update(reason_code="X") == 0
    assert SensorSourceChange.objects.none().update() == 0

    pause_mqtt_deliveries(reason_code="OPS_TEST", process_id="pytest")
    event = IotMqttDeliveryPauseEvent.objects.get()
    with pytest.raises(ValidationError):
        IotMqttDeliveryPauseEvent.objects.filter(pk=event.pk).update(reason_code="ALTERED")
    event.refresh_from_db()
    assert event.reason_code == "OPS_TEST"


def test_user_with_iot_audit_history_cannot_be_deleted(admin, farm_id, make_user):
    operator = make_user("55555555")
    coordinator = iot.create_coordinator(user=admin, farm_id=farm_id, name="C")
    pause_mqtt_deliveries(
        reason_code="OPS_TEST", coordinator_id=coordinator.pk, actor=operator
    )
    event = IotMqttDeliveryPauseEvent.objects.get()

    with pytest.raises(ValidationError):
        operator.delete()

    assert User.objects.filter(pk=operator.pk).exists()
    event.refresh_from_db()
    assert event.actor_id == operator.pk  # history not rewritten
