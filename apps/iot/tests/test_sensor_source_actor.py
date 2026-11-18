# ruff: noqa: S101
"""BUILD-IOT-FIX-01: lifecycle of the source audit actor session variable.

Every scenario starts from a stale value so a NULL afterwards proves that the
cleanup actually ran, not merely that the variable was never assigned.
"""

import logging

import pytest
from django.db import OperationalError, ProgrammingError, connection, transaction
from django.db.transaction import TransactionManagementError

from apps.iot import services
from apps.iot.models import SensorSourceChange
from apps.iot.services import sensor_sources
from apps.iot.services.sensor_sources import _source_change_actor

pytestmark = pytest.mark.django_db

STALE_ACTOR = 987654321
ASSIGN_SQL = "SET @agrotech_iot_source_actor_id = %s"


def _actor():
    with connection.cursor() as cursor:
        cursor.execute("SELECT @agrotech_iot_source_actor_id")
        value = cursor.fetchone()[0]
    return None if value is None else int(value)


def _set_stale_actor():
    with connection.cursor() as cursor:
        cursor.execute(ASSIGN_SQL, [STALE_ACTOR])
    assert _actor() == STALE_ACTOR


def _assert_session_discarded(raw_connection):
    # Inside atomic() Django closes the physical session but keeps the
    # reference and flags closed_in_transaction; outside it drops the reference.
    assert raw_connection is not None
    assert connection.connection is None or connection.closed_in_transaction
    assert getattr(raw_connection, "open", None) is False


def test_actor_is_assigned_inside_and_cleared_after_success(admin):
    _set_stale_actor()
    with _source_change_actor(admin.pk):
        assert _actor() == admin.pk
    assert _actor() is None


def test_update_sensor_source_audits_actor_and_leaves_session_clean(admin, farm_id):
    coordinator = services.create_coordinator(user=admin, farm_id=farm_id, name="Coord")
    source = services.create_sensor_source(
        user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid,
        name="Fuente", role="primary",
    )
    _set_stale_actor()

    services.update_sensor_source(
        user=admin, farm_id=farm_id, source_uid=source.uid, description="Revisada"
    )

    change = SensorSourceChange.objects.get(source=source)
    assert change.changed_by_id == admin.pk
    assert change.after_data["description"] == "Revisada"
    assert _actor() is None


def test_actor_is_cleared_and_error_propagates_when_operation_fails(admin):
    _set_stale_actor()
    original = ValueError("operación protegida fallida")
    with pytest.raises(ValueError) as excinfo:
        with _source_change_actor(admin.pk):
            assert _actor() == admin.pk
            raise original
    assert excinfo.value is original
    assert _actor() is None


def test_actor_is_cleared_after_failed_query_breaks_the_atomic_block(admin):
    """Same mechanism as a trigger SIGNAL during Model.save() inside atomic()."""
    _set_stale_actor()
    with pytest.raises(ProgrammingError) as excinfo:
        with transaction.atomic():
            with _source_change_actor(admin.pk):
                with transaction.mark_for_rollback_on_error():
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT 1 FROM agrotech_fix01_missing_table")
                assert connection.needs_rollback, "the scenario must break the transaction"
    # The database error, not Django's broken-transaction guard, must surface.
    assert not isinstance(excinfo.value, TransactionManagementError)
    assert _actor() is None


def test_cleanup_runs_when_initial_assignment_fails(admin):
    _set_stale_actor()
    body_ran = []

    def fail_assignment(execute, sql, params, many, context):
        if sql == ASSIGN_SQL and params == [admin.pk]:
            raise OperationalError("asignación simulada fallida")
        return execute(sql, params, many, context)

    with connection.execute_wrapper(fail_assignment):
        with pytest.raises(OperationalError, match="asignación simulada fallida"):
            with _source_change_actor(admin.pk):
                body_ran.append(True)
    assert body_ran == []
    assert _actor() is None  # the stale value was cleared by the cleanup


def test_cleanup_failure_does_not_mask_original_exception(admin, monkeypatch, caplog):
    def broken_reset(_raw_connection):
        raise OperationalError("limpieza simulada fallida")

    monkeypatch.setattr(sensor_sources, "_reset_source_actor_variable", broken_reset)
    original = ValueError("error original")
    with caplog.at_level(logging.ERROR, logger=sensor_sources.__name__):
        with pytest.raises(ValueError) as excinfo:
            with _source_change_actor(admin.pk):
                raw_connection = connection.connection
                raise original
    assert excinfo.value is original
    assert "no se pudo limpiar el actor" in caplog.text
    # Fallback: the session holding the stale actor is discarded.
    _assert_session_discarded(raw_connection)


def test_cleanup_failure_after_success_is_raised_and_session_discarded(admin, monkeypatch):
    def broken_reset(_raw_connection):
        raise OperationalError("limpieza simulada fallida")

    monkeypatch.setattr(sensor_sources, "_reset_source_actor_variable", broken_reset)
    with pytest.raises(OperationalError, match="limpieza simulada fallida"):
        with _source_change_actor(admin.pk):
            raw_connection = connection.connection
    _assert_session_discarded(raw_connection)
