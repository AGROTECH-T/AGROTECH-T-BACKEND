"""MariaDB guards making accepted frames and conflicts append-only."""

import re

from django.db import NotSupportedError

from apps.iot.migrations._rollback_guard import require_destructive_rollback_opt_in

TRIGGERS = (
    (
        "iot_received_frames_bu_immutable",
        "iot_received_frames",
        "UPDATE",
        "IoT received frames are immutable",
    ),
    (
        "iot_received_frames_bd_immutable",
        "iot_received_frames",
        "DELETE",
        "IoT received frames cannot be deleted",
    ),
    (
        "iot_frame_conflicts_bu_immutable",
        "iot_frame_conflicts",
        "UPDATE",
        "IoT frame conflicts are immutable",
    ),
    (
        "iot_frame_conflicts_bd_immutable",
        "iot_frame_conflicts",
        "DELETE",
        "IoT frame conflicts cannot be deleted",
    ),
)


def _normalize_sql(value):
    return re.sub(r"\s+", " ", value or "").strip().rstrip(";").casefold()


def _expected_statement(message):
    return _normalize_sql(
        f"BEGIN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '{message}'; END"
    )


def _trigger_state(connection, name):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT EVENT_OBJECT_TABLE, ACTION_TIMING, EVENT_MANIPULATION, ACTION_STATEMENT "
            "FROM information_schema.TRIGGERS "
            "WHERE TRIGGER_SCHEMA = DATABASE() AND TRIGGER_NAME = %s",
            [name],
        )
        return cursor.fetchone()


def install_ingestion_history_triggers(apps, schema_editor):
    """Install only missing guards, refusing homonymous foreign definitions."""
    connection = schema_editor.connection
    if not getattr(connection, "mysql_is_mariadb", False):
        raise NotSupportedError("iot.0013 requiere MariaDB 11.8")
    version = tuple(getattr(connection, "mysql_version", ()) or ())
    if version[:2] != (11, 8):
        raise NotSupportedError("iot.0013 requiere MariaDB 11.8")

    quote = schema_editor.quote_name
    missing = []
    for name, table, event, message in TRIGGERS:
        found = _trigger_state(connection, name)
        expected = (table, "BEFORE", event, _expected_statement(message))
        if found is not None:
            actual = (found[0], found[1], found[2], _normalize_sql(found[3]))
            if actual != expected:
                raise RuntimeError(
                    f"El trigger {name} existe con otra definición; no se reemplazará."
                )
        else:
            missing.append((name, table, event, message))

    for name, table, event, message in missing:
        statement = (
            f"BEGIN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '{message}'; END"
        )
        schema_editor.execute(
            f"CREATE TRIGGER {quote(name)} BEFORE {event} ON {quote(table)} "
            f"FOR EACH ROW {statement}"
        )


def uninstall_ingestion_history_triggers(apps, schema_editor):
    """Drop only our own guards, and only behind the explicit rollback opt-in.

    Removing the triggers loses no rows, but it removes the protection of
    accepted evidence, so it is refused by default (see ``_rollback_guard``).
    Unknown or altered homonymous triggers are never dropped.
    """
    require_destructive_rollback_opt_in("iot.0013")
    connection = schema_editor.connection
    if not getattr(connection, "mysql_is_mariadb", False):
        raise NotSupportedError("iot.0013 requiere MariaDB 11.8")
    version = tuple(getattr(connection, "mysql_version", ()) or ())
    if version[:2] != (11, 8):
        raise NotSupportedError("iot.0013 requiere MariaDB 11.8")
    quote = schema_editor.quote_name
    for name, table, event, message in reversed(TRIGGERS):
        found = _trigger_state(connection, name)
        if found is None:
            continue
        actual = (found[0], found[1], found[2], _normalize_sql(found[3]))
        if actual != (table, "BEFORE", event, _expected_statement(message)):
            raise RuntimeError(
                f"El trigger {name} tiene una definición distinta; no se eliminará."
            )
        schema_editor.execute(f"DROP TRIGGER {quote(name)}")
