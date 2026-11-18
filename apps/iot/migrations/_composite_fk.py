"""Idempotent, reversible composite foreign-key operations for IoT migrations."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ForeignKeySpec:
    """A complete composite foreign-key definition."""

    name: str
    table: str
    columns: tuple[str, ...]
    target_table: str
    target_columns: tuple[str, ...]

    def __post_init__(self):
        if not self.columns or len(self.columns) != len(self.target_columns):
            raise ValueError("Una FK compuesta requiere igual número de columnas origen y destino.")


SUPPORTED_VENDORS = frozenset({"mysql", "postgresql"})
FK_EXISTS_SQL = {
    "mysql": (
        "SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS "
        "WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME = %s "
        "AND CONSTRAINT_NAME = %s AND CONSTRAINT_TYPE = 'FOREIGN KEY'"
    ),
    "postgresql": (
        "SELECT COUNT(*) FROM information_schema.table_constraints "
        "WHERE constraint_schema = current_schema() AND table_name = %s "
        "AND constraint_name = %s AND constraint_type = 'FOREIGN KEY'"
    ),
}
FK_DEFINITION_SQL = {
    "mysql": (
        "SELECT k.COLUMN_NAME, k.REFERENCED_TABLE_NAME, k.REFERENCED_COLUMN_NAME, "
        "r.UPDATE_RULE, r.DELETE_RULE "
        "FROM information_schema.KEY_COLUMN_USAGE k "
        "JOIN information_schema.REFERENTIAL_CONSTRAINTS r "
        "ON r.CONSTRAINT_SCHEMA = k.CONSTRAINT_SCHEMA "
        "AND r.TABLE_NAME = k.TABLE_NAME AND r.CONSTRAINT_NAME = k.CONSTRAINT_NAME "
        "WHERE k.CONSTRAINT_SCHEMA = DATABASE() AND k.TABLE_NAME = %s "
        "AND k.CONSTRAINT_NAME = %s ORDER BY k.ORDINAL_POSITION"
    ),
    "postgresql": (
        "SELECT k.column_name, k.referenced_table_name, k.referenced_column_name, "
        "r.update_rule, r.delete_rule "
        "FROM information_schema.key_column_usage k "
        "JOIN information_schema.referential_constraints r "
        "ON r.constraint_catalog = k.constraint_catalog "
        "AND r.constraint_schema = k.constraint_schema "
        "AND r.constraint_name = k.constraint_name "
        "WHERE k.constraint_schema = current_schema() AND k.table_name = %s "
        "AND k.constraint_name = %s ORDER BY k.ordinal_position"
    ),
}


def expected_definition(spec: ForeignKeySpec) -> list[tuple[str, str, str, str, str]]:
    """Expected ordered columns, target columns, and referential actions."""
    return [
        (column, spec.target_table, target_column, "RESTRICT", "RESTRICT")
        for column, target_column in zip(spec.columns, spec.target_columns, strict=True)
    ]


def fk_definition(connection, table: str, name: str):
    """Return the complete FK definition, or ``None`` when the name is absent."""
    vendor = connection.vendor
    if vendor not in SUPPORTED_VENDORS:
        return None
    with connection.cursor() as cursor:
        cursor.execute(FK_EXISTS_SQL[vendor], [table, name])
        if not cursor.fetchone()[0]:
            return None
        cursor.execute(FK_DEFINITION_SQL[vendor], [table, name])
        return [tuple(row) for row in cursor.fetchall()]


def _state(connection, spec: ForeignKeySpec) -> str:
    """Return ``absent``, ``ours`` or ``foreign`` after checking every detail."""
    found = fk_definition(connection, spec.table, spec.name)
    if found is None:
        return "absent"
    return "ours" if found == expected_definition(spec) else "foreign"


def _refuse(spec: ForeignKeySpec, migration: str):
    raise RuntimeError(
        f"La restricción {spec.name} de {spec.table} existe con una definición distinta; "
        f"no pertenece a {migration}. Revísela manualmente antes de reintentar."
    )


def add_foreign_keys(apps, schema_editor, specs, *, migration: str):
    """Create missing N-column foreign keys; reject same-name foreign definitions."""
    connection = schema_editor.connection
    if connection.vendor not in SUPPORTED_VENDORS:
        return
    quote = schema_editor.quote_name
    for spec in specs:
        state = _state(connection, spec)
        if state == "ours":
            continue
        if state == "foreign":
            _refuse(spec, migration)
        columns = ", ".join(quote(column) for column in spec.columns)
        target_columns = ", ".join(quote(column) for column in spec.target_columns)
        schema_editor.execute(
            f"ALTER TABLE {quote(spec.table)} ADD CONSTRAINT {quote(spec.name)} "
            f"FOREIGN KEY ({columns}) REFERENCES {quote(spec.target_table)} "
            f"({target_columns}) ON UPDATE RESTRICT ON DELETE RESTRICT"
        )


def drop_foreign_keys(apps, schema_editor, specs, *, migration: str):
    """Drop only matching N-column foreign keys, in reverse dependency order."""
    connection = schema_editor.connection
    vendor = connection.vendor
    if vendor not in SUPPORTED_VENDORS:
        return
    quote = schema_editor.quote_name
    drop = "DROP FOREIGN KEY" if vendor == "mysql" else "DROP CONSTRAINT"
    for spec in reversed(tuple(specs)):
        state = _state(connection, spec)
        if state == "absent":
            continue
        if state == "foreign":
            _refuse(spec, migration)
        schema_editor.execute(f"ALTER TABLE {quote(spec.table)} {drop} {quote(spec.name)}")
