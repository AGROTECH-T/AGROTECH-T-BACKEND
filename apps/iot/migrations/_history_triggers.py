"""MariaDB 11.8 triggers for immutable IoT history and correction sequencing."""

import re

from django.db import NotSupportedError


TRIGGERS = (
    (
        "iot_measurements_bu_immutable",
        "iot_measurements",
        "BEFORE",
        "UPDATE",
        """BEGIN
  SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'IoT measurements are immutable';
END""",
    ),
    (
        "iot_measurements_bd_immutable",
        "iot_measurements",
        "BEFORE",
        "DELETE",
        """BEGIN
  SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'IoT measurements cannot be deleted';
END""",
    ),
    (
        "iot_measurements_bi_sensor_source",
        "iot_measurements",
        "BEFORE",
        "INSERT",
        """BEGIN
  DECLARE v_source_id BIGINT DEFAULT NULL;
  DECLARE v_sensor_active TINYINT DEFAULT NULL;
  DECLARE v_source_active TINYINT DEFAULT NULL;
  DECLARE CONTINUE HANDLER FOR NOT FOUND SET v_sensor_active = NULL;
  SELECT source_id, is_active INTO v_source_id, v_sensor_active
    FROM iot_sensors WHERE id = NEW.sensor_id FOR UPDATE;
  IF v_sensor_active IS NULL OR v_sensor_active <> 1 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'IoT measurement requires an active sensor';
  END IF;
  IF v_source_id IS NOT NULL THEN
    SELECT is_active INTO v_source_active
      FROM iot_sensor_sources WHERE id = v_source_id FOR UPDATE;
    IF v_source_active IS NULL OR v_source_active <> 1 THEN
      SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'IoT measurement source is inactive';
    END IF;
  END IF;
END""",
    ),
    (
        "iot_sensors_bi_source_active",
        "iot_sensors",
        "BEFORE",
        "INSERT",
        """BEGIN
  DECLARE v_source_active TINYINT DEFAULT NULL;
  IF NEW.source_id IS NOT NULL THEN
    SELECT is_active INTO v_source_active
      FROM iot_sensor_sources WHERE id = NEW.source_id FOR UPDATE;
    IF v_source_active IS NULL OR v_source_active <> 1 THEN
      SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'IoT sensor source must be active';
    END IF;
  END IF;
END""",
    ),
    (
        "iot_sensors_bu_identity",
        "iot_sensors",
        "BEFORE",
        "UPDATE",
        """BEGIN
  IF NOT (OLD.uid <=> NEW.uid)
     OR NOT (OLD.farm_id <=> NEW.farm_id)
     OR NOT (OLD.coordinator_id <=> NEW.coordinator_id) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'IoT sensor identity is immutable';
  END IF;
  IF (NOT (OLD.source_id <=> NEW.source_id)
      OR NOT (OLD.channel <=> NEW.channel)
      OR NOT (OLD.metric <=> NEW.metric))
     AND EXISTS (SELECT 1 FROM iot_measurements m WHERE m.sensor_id = OLD.id) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'IoT sensor has historical measurements';
  END IF;
  IF NEW.is_active = 1 AND NEW.source_id IS NOT NULL THEN
    IF NOT EXISTS (
      SELECT 1 FROM iot_sensor_sources src
      WHERE src.id = NEW.source_id AND src.is_active = 1
    ) THEN
      SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'IoT sensor source must be active';
    END IF;
  END IF;
END""",
    ),
    (
        "iot_sensor_sources_bu_identity",
        "iot_sensor_sources",
        "BEFORE",
        "UPDATE",
        """BEGIN
  DECLARE v_has_measurements TINYINT DEFAULT 0;
  DECLARE v_has_active_sensors TINYINT DEFAULT 0;
  DECLARE v_metadata_changed TINYINT DEFAULT 0;
  DECLARE EXIT HANDLER FOR SQLEXCEPTION
  BEGIN
    SET @agrotech_iot_source_audit_internal = NULL;
    RESIGNAL;
  END;
  IF NOT (OLD.uid <=> NEW.uid)
     OR NOT (OLD.farm_id <=> NEW.farm_id)
     OR NOT (OLD.coordinator_id <=> NEW.coordinator_id) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'IoT sensor source identity is immutable';
  END IF;
  IF NOT (OLD.name <=> NEW.name)
     OR NOT (OLD.model <=> NEW.model)
     OR NOT (OLD.manufacturer <=> NEW.manufacturer)
     OR NOT (OLD.role <=> NEW.role)
     OR NOT (OLD.description <=> NEW.description) THEN
    SET v_metadata_changed = 1;
  END IF;
  IF v_metadata_changed = 1 THEN
    IF @agrotech_iot_source_actor_id IS NULL THEN
      SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'IoT source metadata requires audited service';
    END IF;
    SELECT EXISTS (
      SELECT 1 FROM iot_sensors s
      INNER JOIN iot_measurements m ON m.sensor_id = s.id
      WHERE s.source_id = OLD.id
    ) INTO v_has_measurements;
    IF v_has_measurements = 1
       AND (NOT (OLD.name <=> NEW.name)
         OR NOT (OLD.model <=> NEW.model)
         OR NOT (OLD.manufacturer <=> NEW.manufacturer)
         OR NOT (OLD.role <=> NEW.role)) THEN
      SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'IoT source identity has historical measurements';
    END IF;
    SET @agrotech_iot_source_audit_internal = 1;
    INSERT INTO iot_sensor_source_changes
      (source_id, changed_by_id, changed_at, before_data, after_data)
    VALUES
      (OLD.id, @agrotech_iot_source_actor_id, UTC_TIMESTAMP(6),
       JSON_OBJECT('uid', OLD.uid, 'name', OLD.name, 'model', OLD.model,
                   'manufacturer', OLD.manufacturer, 'role', OLD.role,
                   'description', OLD.description),
       JSON_OBJECT('uid', NEW.uid, 'name', NEW.name, 'model', NEW.model,
                   'manufacturer', NEW.manufacturer, 'role', NEW.role,
                   'description', NEW.description));
    SET @agrotech_iot_source_audit_internal = NULL;
  END IF;
  IF OLD.is_active = 1 AND NEW.is_active = 0 THEN
    SELECT EXISTS (
      SELECT 1 FROM iot_sensors s
      WHERE s.source_id = OLD.id AND s.is_active = 1
    ) INTO v_has_active_sensors;
    IF v_has_active_sensors = 1 THEN
      SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Deactivate source sensors before the source';
    END IF;
  END IF;
END""",
    ),
    (
        "iot_sensor_source_changes_bi_audit",
        "iot_sensor_source_changes",
        "BEFORE",
        "INSERT",
        """BEGIN
  IF COALESCE(@agrotech_iot_source_audit_internal, 0) <> 1 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'IoT source audit rows are trigger-managed';
  END IF;
END""",
    ),
    (
        "iot_sensor_source_changes_bu_immutable",
        "iot_sensor_source_changes",
        "BEFORE",
        "UPDATE",
        """BEGIN
  SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'IoT source audit rows are immutable';
END""",
    ),
    (
        "iot_sensor_source_changes_bd_immutable",
        "iot_sensor_source_changes",
        "BEFORE",
        "DELETE",
        """BEGIN
  SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'IoT source audit rows cannot be deleted';
END""",
    ),
    (
        "iot_sensor_sources_bd_measurements",
        "iot_sensor_sources",
        "BEFORE",
        "DELETE",
        """BEGIN
  SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'IoT sensor sources cannot be deleted';
END""",
    ),
    (
        "iot_measurement_corrections_bi_sequence",
        "iot_measurement_corrections",
        "BEFORE",
        "INSERT",
        """BEGIN
  DECLARE v_measurement_id BIGINT DEFAULT NULL;
  DECLARE v_last_revision INT UNSIGNED DEFAULT 0;
  DECLARE CONTINUE HANDLER FOR NOT FOUND SET v_measurement_id = NULL;
  DECLARE EXIT HANDLER FOR SQLEXCEPTION
  BEGIN
    SET @agrotech_iot_correction_sequence_internal = NULL;
    RESIGNAL;
  END;
  SELECT id INTO v_measurement_id
    FROM iot_measurements WHERE id = NEW.measurement_id FOR UPDATE;
  IF v_measurement_id IS NULL THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'IoT correction measurement does not exist';
  END IF;
  SET @agrotech_iot_correction_sequence_internal = 1;
  INSERT INTO iot_measurement_correction_sequences (measurement_id, last_revision)
    VALUES (NEW.measurement_id, 0)
    ON DUPLICATE KEY UPDATE measurement_id = VALUES(measurement_id);
  SELECT last_revision INTO v_last_revision
    FROM iot_measurement_correction_sequences
    WHERE measurement_id = NEW.measurement_id FOR UPDATE;
  IF NEW.revision <> v_last_revision + 1 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'IoT correction revision is out of sequence';
  END IF;
  IF (v_last_revision = 0 AND NEW.previous_correction_id IS NOT NULL)
     OR (v_last_revision > 0 AND NEW.previous_correction_id IS NULL) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'IoT correction predecessor is invalid';
  END IF;
  UPDATE iot_measurement_correction_sequences
    SET last_revision = NEW.revision
    WHERE measurement_id = NEW.measurement_id;
  SET @agrotech_iot_correction_sequence_internal = NULL;
END""",
    ),
    (
        "iot_measurement_corrections_bu_immutable",
        "iot_measurement_corrections",
        "BEFORE",
        "UPDATE",
        """BEGIN
  SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'IoT corrections are immutable';
END""",
    ),
    (
        "iot_measurement_corrections_bd_immutable",
        "iot_measurement_corrections",
        "BEFORE",
        "DELETE",
        """BEGIN
  SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'IoT corrections cannot be deleted';
END""",
    ),
    (
        "iot_measurement_correction_sequences_bi_guard",
        "iot_measurement_correction_sequences",
        "BEFORE",
        "INSERT",
        """BEGIN
  IF COALESCE(@agrotech_iot_correction_sequence_internal, 0) <> 1 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'IoT correction sequence is trigger-managed';
  END IF;
END""",
    ),
    (
        "iot_measurement_correction_sequences_bu_guard",
        "iot_measurement_correction_sequences",
        "BEFORE",
        "UPDATE",
        """BEGIN
  IF COALESCE(@agrotech_iot_correction_sequence_internal, 0) <> 1 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'IoT correction sequence is trigger-managed';
  END IF;
END""",
    ),
    (
        "iot_measurement_correction_sequences_bd_guard",
        "iot_measurement_correction_sequences",
        "BEFORE",
        "DELETE",
        """BEGIN
  SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'IoT correction sequence cannot be deleted';
END""",
    ),
)


def _assert_mariadb_118(connection):
    if not getattr(connection, "mysql_is_mariadb", False):
        raise NotSupportedError("IOT-SOURCES-02 requiere MariaDB 11.8 para sus triggers")
    version = tuple(getattr(connection, "mysql_version", ()) or ())
    if version[:2] != (11, 8):
        raise NotSupportedError(
            "IOT-SOURCES-02 requiere MariaDB 11.8; la migración no instalará protección parcial"
        )


def _normalize_sql(value):
    return re.sub(r"\s+", " ", value or "").strip().rstrip(";").casefold()


def _trigger_state(connection, name):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT EVENT_OBJECT_TABLE, ACTION_TIMING, EVENT_MANIPULATION, ACTION_STATEMENT "
            "FROM information_schema.TRIGGERS "
            "WHERE TRIGGER_SCHEMA = DATABASE() AND TRIGGER_NAME = %s",
            [name],
        )
        return cursor.fetchone()


def install_history_triggers(apps, schema_editor):
    connection = schema_editor.connection
    _assert_mariadb_118(connection)
    quote = schema_editor.quote_name
    for name, table, timing, event, body in TRIGGERS:
        found = _trigger_state(connection, name)
        expected = (table, timing, event, _normalize_sql(body))
        if found is not None:
            actual = (found[0], found[1], found[2], _normalize_sql(found[3]))
            if actual != expected:
                raise RuntimeError(
                    f"El trigger {name} ya existe con otra definición; "
                    "la migración no lo reemplazará."
                )
            continue
        schema_editor.execute(
            f"CREATE TRIGGER {quote(name)} {timing} {event} ON {quote(table)} "
            f"FOR EACH ROW {body}"
        )


def uninstall_history_triggers(apps, schema_editor):
    connection = schema_editor.connection
    _assert_mariadb_118(connection)
    quote = schema_editor.quote_name
    for name, table, timing, event, body in reversed(TRIGGERS):
        found = _trigger_state(connection, name)
        if found is None:
            continue
        actual = (found[0], found[1], found[2], _normalize_sql(found[3]))
        expected = (table, timing, event, _normalize_sql(body))
        if actual != expected:
            raise RuntimeError(
                f"El trigger {name} tiene una definición distinta; "
                "la reversión no lo eliminará."
            )
        schema_editor.execute(f"DROP TRIGGER {quote(name)}")
