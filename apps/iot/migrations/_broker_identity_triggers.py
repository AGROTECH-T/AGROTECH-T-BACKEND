"""MariaDB append-only guards for MQTT delivery events and broker fingerprints.

BUILD-FLEPSI-02. Same pattern as ``_ingestion_history_triggers``: MariaDB 11.8
only, install only missing triggers, refuse a homonymous trigger with another
definition, retryable (``atomic=False``). Removal requires the explicit
rollback opt-in (``_rollback_guard``) and only drops our own definitions.

The ORM guard (``_AppendOnlyQuerySet``) does not cover SQL issued outside it,
e.g. Django's cascade fast-delete (``_raw_delete``); these triggers do.
"""

import re

from django.db import NotSupportedError

from apps.iot.migrations._rollback_guard import require_destructive_rollback_opt_in

TRIGGERS = (
    (
        "iot_mqtt_delivery_events_bu_immutable",
        "iot_mqtt_delivery_events",
        "UPDATE",
        "IoT MQTT delivery events are immutable",
    ),
    (
        "iot_mqtt_delivery_events_bd_immutable",
        "iot_mqtt_delivery_events",
        "DELETE",
        "IoT MQTT delivery events cannot be deleted",
    ),
    (
        "iot_broker_identity_fps_bu_immutable",
        "iot_broker_identity_fingerprints",
        "UPDATE",
        "IoT broker identity fingerprints are immutable",
    ),
    (
        "iot_broker_identity_fps_bd_immutable",
        "iot_broker_identity_fingerprints",
        "DELETE",
        "IoT broker identity fingerprints cannot be deleted",
    ),
)


def _normalize_sql(value):
    return re.sub(r"\s+", " ", value or "").strip().rstrip(";").casefold()


def _statement(message):
    return f"BEGIN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '{message}'; END"


def _trigger_state(connection, name):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT EVENT_OBJECT_TABLE, ACTION_TIMING, EVENT_MANIPULATION, ACTION_STATEMENT "
            "FROM information_schema.TRIGGERS "
            "WHERE TRIGGER_SCHEMA = DATABASE() AND TRIGGER_NAME = %s",
            [name],
        )
        return cursor.fetchone()


def _assert_mariadb_118(connection):
    if not getattr(connection, "mysql_is_mariadb", False):
        raise NotSupportedError("iot.0019 requiere MariaDB 11.8")
    version = tuple(getattr(connection, "mysql_version", ()) or ())
    if version[:2] != (11, 8):
        raise NotSupportedError("iot.0019 requiere MariaDB 11.8")


def _matches(found, table, event, message):
    actual = (found[0], found[1], found[2], _normalize_sql(found[3]))
    return actual == (table, "BEFORE", event, _normalize_sql(_statement(message)))


def install_broker_identity_triggers(apps, schema_editor):
    connection = schema_editor.connection
    _assert_mariadb_118(connection)
    quote = schema_editor.quote_name
    missing = []
    for name, table, event, message in TRIGGERS:
        found = _trigger_state(connection, name)
        if found is None:
            missing.append((name, table, event, message))
        elif not _matches(found, table, event, message):
            raise RuntimeError(f"El trigger {name} existe con otra definición; no se reemplazará.")
    for name, table, event, message in missing:
        schema_editor.execute(
            f"CREATE TRIGGER {quote(name)} BEFORE {event} ON {quote(table)} "
            f"FOR EACH ROW {_statement(message)}"
        )


def uninstall_broker_identity_triggers(apps, schema_editor):
    require_destructive_rollback_opt_in("iot.0019")
    connection = schema_editor.connection
    _assert_mariadb_118(connection)
    quote = schema_editor.quote_name
    for name, table, event, message in reversed(TRIGGERS):
        found = _trigger_state(connection, name)
        if found is None:
            continue
        if not _matches(found, table, event, message):
            raise RuntimeError(f"El trigger {name} tiene una definición distinta; no se eliminará.")
        schema_editor.execute(f"DROP TRIGGER {quote(name)}")
