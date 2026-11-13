"""Preflight de la base desechable y bloqueo de correo para el orquestador.

Propósito: demostrar, con la conexión real de Django, que el proceso está
conectado a la MariaDB desechable de la ejecución actual antes de permitir
cualquier migración o prueba, y bloquear todo envío de correo real.

Contexto: lo invoca ``tools/run_permissions_disposable.ps1`` como
``python -B -E -s -X utf8 -m config.disposable_preflight <subcomando>`` y
también se carga como plugin de pytest (``-p config.disposable_preflight``)
para instalar el bloqueo de correo en el proceso de pruebas. Los subcomandos
``migrate`` y ``sqlmigrate`` ejecutan el preflight en el mismo proceso y solo
continúan si el resultado es ``OK``; el orquestador no invoca ``manage.py``.

Ruta adicional de migración (AUD-002): en la etapa de pruebas, pytest-django
con ``--reuse-db`` llama a ``setup_databases(keepdb=True)``, que vuelve a
ejecutar ``migrate`` dentro del proceso de pytest. Esa ruta NO ejecuta este
preflight completo (privilegios y esquema); la protegen la guarda existente de
``conftest.py`` (``--reuse-db`` obligatorio, ``--create-db`` prohibido y
``validate_live_disposable_database`` antes de ``django_db_setup``) y el
subcomando ``preflight --expect-schema migrated`` que el orquestador ejecuta
justo antes de pytest. Con las migraciones ya aplicadas, esa llamada no debería
cambiar el esquema.

Las reglas de identidad se reutilizan de ``config.settings_test_disposable``
(constantes, ``validate_disposable_environment`` y
``validate_live_disposable_database``); este módulo añade la comprobación de
privilegios por ``SHOW GRANTS``, del estado del esquema y del bloqueo de correo.
Ningún resultado incluye contraseñas, hashes de contraseña ni tokens.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ALLOWED_SETTINGS_MODULES = (
    "config.settings_test_disposable",
    "config.settings_test_disposable_isolated",
)
ISOLATED_SETTINGS_MODULE = "config.settings_test_disposable_isolated"
EXPLICIT_PROTECTED_DATABASES = frozenset({"agrotech_db", "agrotech_t"})
EXPECTED_ENGINE = "django.db.backends.mysql"
EXPECTED_STORAGE_ENGINE = "innodb"
EXPECTED_SERVER_VERSION_PREFIX = "11.8.8"
LOCMEM_EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
REPORT_DIR_ENV = "AGROTECH_DISPOSABLE_REPORT_DIR"
REDIS_ENABLED_ENV = "AGROTECH_DISPOSABLE_REDIS"
DOTENV_DISABLED_ENV = "PYTHON_DOTENV_DISABLED"
MAIL_ENV_MUST_BE_EMPTY = (
    "SMTP_HOST",
    "SMTP_USER",
    "SMTP_PASSWORD",
    "SMTP_FROM",
    "BREVO_API_KEY",
)
REQUIRED_SCHEMA_PRIVILEGES = frozenset(
    {
        "SELECT",
        "INSERT",
        "UPDATE",
        "DELETE",
        "CREATE",
        "ALTER",
        "INDEX",
        "DROP",
        "REFERENCES",
    }
)

# Tabla de códigos de salida de los procesos hijos. Es un subconjunto de la
# tabla central de ``tools/disposable_orchestrator_lib.ps1``; las pruebas de
# ambos lados verifican que los valores coinciden.
EXIT_CODES = {
    "OK": 0,
    "PREFLIGHT_REJECTED": 10,
    "MIGRATION_FAILED": 20,
    "INFRASTRUCTURE_FAILED": 40,
}

STATUS_OK = "OK"
STATUS_CONFIG_NOT_ALLOWED = "CONFIG_NOT_ALLOWED"
STATUS_PROTECTED_DATABASE = "PROTECTED_DATABASE"
STATUS_SERVER_IDENTITY_MISMATCH = "SERVER_IDENTITY_MISMATCH"
STATUS_INSUFFICIENT_PRIVILEGES = "INSUFFICIENT_PRIVILEGES"
STATUS_EXCESSIVE_PRIVILEGES = "EXCESSIVE_PRIVILEGES"
STATUS_UNEXPECTED_SCHEMA = "UNEXPECTED_SCHEMA"
STATUS_CONNECTION_ERROR = "CONNECTION_ERROR"
STATUS_MAIL_NOT_BLOCKED = "MAIL_NOT_BLOCKED"
STATUS_REDIS_NOT_ALLOWED = "REDIS_NOT_ALLOWED"
STATUS_INTERNAL_ERROR = "INTERNAL_ERROR"

REJECTION_STATUSES = frozenset(
    {
        STATUS_CONFIG_NOT_ALLOWED,
        STATUS_PROTECTED_DATABASE,
        STATUS_SERVER_IDENTITY_MISMATCH,
        STATUS_INSUFFICIENT_PRIVILEGES,
        STATUS_EXCESSIVE_PRIVILEGES,
        STATUS_UNEXPECTED_SCHEMA,
        STATUS_CONNECTION_ERROR,
        STATUS_MAIL_NOT_BLOCKED,
        STATUS_REDIS_NOT_ALLOWED,
    }
)

SCHEMA_EMPTY = "empty"
SCHEMA_MIGRATED = "migrated"


class PreflightRejected(Exception):
    """Rechazo controlado del preflight con un estado estable."""

    def __init__(self, status: str, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


class MailBlocked(RuntimeError):
    """Intento de envío real bloqueado en una ejecución desechable."""


@dataclass(frozen=True)
class ExpectedIdentity:
    """Identidad esperada del destino desechable de esta ejecución."""

    settings_module: str
    run_id: str
    database: str
    user: str
    host: str
    port: str
    server_port: str
    hostname: str
    protected: frozenset


@dataclass
class PreflightResult:
    """Resultado verificable del preflight, sin datos sensibles."""

    status: str = STATUS_OK
    detail: str = ""
    checks: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "detail": self.detail,
            "exit_code": exit_code_for_status(self.status),
            "checks": list(self.checks),
        }


def exit_code_for_status(status: str) -> int:
    """Traduce un estado del preflight a la tabla de códigos de salida."""
    if status == STATUS_OK:
        return EXIT_CODES["OK"]
    if status in REJECTION_STATUSES:
        return EXIT_CODES["PREFLIGHT_REJECTED"]
    return EXIT_CODES["INFRASTRUCTURE_FAILED"]


# ---------------------------------------------------------------------------
# Comprobaciones puras (sin conexión), reutilizables por las pruebas unitarias.
# ---------------------------------------------------------------------------


def check_settings_module(requested, environ_value, configured_value=None) -> None:
    """Exige un módulo de la lista blanca, idéntico en argumento y entorno."""
    if not requested or requested not in ALLOWED_SETTINGS_MODULES:
        raise PreflightRejected(
            STATUS_CONFIG_NOT_ALLOWED, f"módulo no autorizado: {requested!r}"
        )
    if environ_value != requested:
        raise PreflightRejected(
            STATUS_CONFIG_NOT_ALLOWED,
            "DJANGO_SETTINGS_MODULE no coincide con el módulo solicitado",
        )
    if configured_value is not None and configured_value != requested:
        raise PreflightRejected(
            STATUS_CONFIG_NOT_ALLOWED,
            "la configuración cargada no coincide con el módulo solicitado",
        )


def _is_protected(name, protected) -> bool:
    return isinstance(name, str) and name.strip().lower() in {
        item.lower() for item in protected
    }


def check_connection_settings(databases: dict, expected: ExpectedIdentity) -> None:
    """Compara los atributos de conexión configurados con la identidad esperada."""
    if set(databases) != {"default"}:
        raise PreflightRejected(
            STATUS_CONFIG_NOT_ALLOWED, "solo se admite el alias de base 'default'"
        )
    default = databases["default"] or {}
    name = default.get("NAME")
    test_name = (default.get("TEST") or {}).get("NAME")
    if _is_protected(name, expected.protected) or _is_protected(
        test_name, expected.protected
    ):
        raise PreflightRejected(
            STATUS_PROTECTED_DATABASE, "la configuración apunta a una base protegida"
        )
    problems = []
    if default.get("ENGINE") != EXPECTED_ENGINE:
        problems.append("motor de Django inesperado")
    if name != expected.database or test_name != expected.database:
        problems.append("nombre de base distinto del esperado")
    if default.get("USER") != expected.user:
        problems.append("usuario distinto del esperado")
    if default.get("HOST") != expected.host:
        problems.append("host distinto del esperado")
    if str(default.get("PORT", "")) != expected.port:
        problems.append("puerto distinto del esperado")
    if problems:
        raise PreflightRejected(STATUS_CONFIG_NOT_ALLOWED, "; ".join(problems))


def check_server_identity(row, expected: ExpectedIdentity) -> None:
    """Valida la identidad del servidor conectado.

    ``row`` = (DATABASE(), CURRENT_USER(), @@port, @@hostname, @@version,
    @@default_storage_engine).
    """
    if not row or len(row) < 6:
        raise PreflightRejected(
            STATUS_SERVER_IDENTITY_MISMATCH, "no se pudo identificar el servidor"
        )
    database, current_user, server_port, hostname, version, storage = row[:6]
    if _is_protected(database, expected.protected):
        raise PreflightRejected(
            STATUS_PROTECTED_DATABASE, "la conexión usa una base protegida"
        )
    actual_user = str(current_user).rsplit("@", 1)[0].strip("'\"`")
    problems = []
    if str(database) != expected.database:
        problems.append("DATABASE() distinta de la esperada")
    if actual_user != expected.user:
        problems.append("CURRENT_USER() distinto del esperado")
    if str(server_port) != expected.server_port:
        problems.append("puerto interno del servidor inesperado")
    if str(hostname) != expected.hostname:
        problems.append("hostname del servidor distinto del contenedor esperado")
    version_text = str(version)
    if "mariadb" not in version_text.lower() or not version_text.startswith(
        EXPECTED_SERVER_VERSION_PREFIX
    ):
        problems.append("versión del servidor distinta de MariaDB 11.8.8")
    if str(storage).lower() != EXPECTED_STORAGE_ENGINE:
        problems.append("motor de almacenamiento distinto de InnoDB")
    if problems:
        raise PreflightRejected(STATUS_SERVER_IDENTITY_MISMATCH, "; ".join(problems))


_GRANT_RE = re.compile(
    r"^GRANT\s+(?P<privs>.+?)\s+ON\s+(?P<target>\S+)\s+TO\s+(?P<rest>.+)$",
    re.IGNORECASE,
)


_SCHEMA_TARGET_RE = re.compile(r"^`(?P<schema>[^`]+)`\.(?:\*|`[^`]+`)$")


def decode_grant_schema(target: str) -> str:
    """Decodifica de forma estricta el destino ```esquema`.*`` de SHOW GRANTS.

    Acepta el escape de MariaDB (``\\_`` y ``\\%``) y el escape duplicado que
    añade el cliente ``mariadb --batch`` sin ``--raw`` (``\\\\_``). Como los
    nombres desechables solo contienen ``[a-z0-9_]``, colapsar ``\\\\`` es
    inequívoco. Rechaza comodines sin escapar (``_`` y ``%`` coinciden con
    otras bases), escapes desconocidos y cualquier formato no reconocido.
    """
    match = _SCHEMA_TARGET_RE.match(target)
    if match is None:
        raise PreflightRejected(
            STATUS_EXCESSIVE_PRIVILEGES, "destino de concesión no reconocido"
        )
    raw = match.group("schema").replace("\\\\", "\\")
    decoded = []
    index = 0
    while index < len(raw):
        char = raw[index]
        if char == "\\":
            following = raw[index + 1] if index + 1 < len(raw) else ""
            if following in ("_", "%"):
                decoded.append(following)
                index += 2
                continue
            raise PreflightRejected(
                STATUS_EXCESSIVE_PRIVILEGES, "escape no reconocido en el destino de concesión"
            )
        if char in ("_", "%"):
            raise PreflightRejected(
                STATUS_EXCESSIVE_PRIVILEGES, "comodín sin escapar en el destino de concesión"
            )
        decoded.append(char)
        index += 1
    return "".join(decoded)


def check_privileges(grant_lines, current_role, applicable_roles: int, database: str) -> None:
    """Exige privilegios solo sobre la base desechable, sin globales ni roles.

    Usa ``SHOW GRANTS FOR CURRENT_USER()``, que no depende del formato de
    ``GRANTEE`` en ``information_schema``. Las líneas nunca se registran: pueden
    contener el hash de la contraseña.
    """
    if current_role not in (None, "", "NONE"):
        raise PreflightRejected(STATUS_EXCESSIVE_PRIVILEGES, "hay un rol activo")
    if applicable_roles:
        raise PreflightRejected(STATUS_EXCESSIVE_PRIVILEGES, "hay roles aplicables")
    schema_privileges = set()
    for raw in grant_lines:
        line = str(raw).strip()
        if not line:
            continue
        match = _GRANT_RE.match(line)
        if match is None:
            raise PreflightRejected(
                STATUS_EXCESSIVE_PRIVILEGES, "concesión no reconocida (posible rol)"
            )
        privs = match.group("privs").upper()
        target = match.group("target")
        rest = match.group("rest").upper()
        if "WITH GRANT OPTION" in rest:
            raise PreflightRejected(STATUS_EXCESSIVE_PRIVILEGES, "GRANT OPTION presente")
        if privs.startswith("PROXY"):
            raise PreflightRejected(STATUS_EXCESSIVE_PRIVILEGES, "privilegio PROXY")
        if target == "*.*":
            if privs != "USAGE":
                raise PreflightRejected(
                    STATUS_EXCESSIVE_PRIVILEGES, "privilegios globales presentes"
                )
            continue
        if decode_grant_schema(target) != database:
            raise PreflightRejected(
                STATUS_EXCESSIVE_PRIVILEGES, "privilegios fuera de la base desechable"
            )
        if not target.endswith(".*"):
            # Concesiones a nivel de tabla o columna dentro de la base: se
            # aceptan solo si no amplían el alcance; no aportan al mínimo.
            continue
        if privs in {"ALL", "ALL PRIVILEGES"}:
            schema_privileges |= REQUIRED_SCHEMA_PRIVILEGES
        else:
            schema_privileges |= {item.strip() for item in privs.split(",")}
    missing = REQUIRED_SCHEMA_PRIVILEGES - schema_privileges
    if missing:
        raise PreflightRejected(
            STATUS_INSUFFICIENT_PRIVILEGES,
            "faltan privilegios mínimos: " + ", ".join(sorted(missing)),
        )


def check_schema(tables, mode: str, known_tables=frozenset(), pending: int = 0) -> None:
    """Valida el estado del esquema según la etapa."""
    table_set = {str(name) for name in tables}
    if mode == SCHEMA_EMPTY:
        if table_set:
            raise PreflightRejected(
                STATUS_UNEXPECTED_SCHEMA,
                f"se esperaba un esquema vacío y hay {len(table_set)} tablas",
            )
        return
    if mode != SCHEMA_MIGRATED:
        raise PreflightRejected(STATUS_UNEXPECTED_SCHEMA, f"modo de esquema inválido: {mode!r}")
    if "django_migrations" not in table_set:
        raise PreflightRejected(STATUS_UNEXPECTED_SCHEMA, "falta django_migrations")
    if pending:
        raise PreflightRejected(
            STATUS_UNEXPECTED_SCHEMA, f"quedan {pending} migraciones sin aplicar"
        )
    unexpected = table_set - set(known_tables) - {"django_migrations"}
    if unexpected:
        raise PreflightRejected(
            STATUS_UNEXPECTED_SCHEMA, f"tablas no esperadas: {len(unexpected)}"
        )


def check_mail_blocked(environ, mail_settings: dict, guards_installed: bool) -> None:
    """Exige el bloqueo de correo: entorno vacío, .env desactivado y backend local."""
    if str(environ.get(DOTENV_DISABLED_ENV, "")).casefold() not in {"1", "true", "yes"}:
        raise PreflightRejected(
            STATUS_MAIL_NOT_BLOCKED, "la carga de .env no está desactivada"
        )
    for name in MAIL_ENV_MUST_BE_EMPTY:
        if environ.get(name, "") != "":
            raise PreflightRejected(
                STATUS_MAIL_NOT_BLOCKED, f"variable de correo no vacía: {name}"
            )
    if mail_settings.get("EMAIL_HOST") or mail_settings.get("EMAIL_HOST_USER"):
        raise PreflightRejected(
            STATUS_MAIL_NOT_BLOCKED, "la configuración efectiva conserva un host SMTP"
        )
    if mail_settings.get("DEFAULT_FROM_EMAIL"):
        raise PreflightRejected(
            STATUS_MAIL_NOT_BLOCKED, "la configuración efectiva conserva un remitente"
        )
    if mail_settings.get("EMAIL_BACKEND") != LOCMEM_EMAIL_BACKEND:
        raise PreflightRejected(
            STATUS_MAIL_NOT_BLOCKED, "el backend de correo no es el de pruebas"
        )
    if not guards_installed:
        raise PreflightRejected(STATUS_MAIL_NOT_BLOCKED, "bloqueo SMTP/HTTP no instalado")


def check_report_dir(report_dir, base_dir) -> Path:
    """Exige un directorio de resultados fuera del repositorio."""
    if not report_dir:
        raise PreflightRejected(STATUS_CONFIG_NOT_ALLOWED, "falta el directorio de resultados")
    resolved = Path(report_dir).resolve()
    base = Path(base_dir).resolve()
    if resolved == base or base in resolved.parents:
        raise PreflightRejected(
            STATUS_CONFIG_NOT_ALLOWED, "el directorio de resultados está dentro del repositorio"
        )
    return resolved


# ---------------------------------------------------------------------------
# Bloqueo de correo (SMTP y HTTP de proveedores) por monkeypatch en proceso.
# ---------------------------------------------------------------------------

_GUARD_STATE = {"installed": False, "smtp_attempts": 0, "http_attempts": 0, "originals": {}}


def install_mail_guards() -> None:
    """Sustituye la conexión SMTP y el envío HTTP de requests por un bloqueo."""
    if _GUARD_STATE["installed"]:
        return
    import smtplib

    originals = _GUARD_STATE["originals"]
    originals["smtp_connect"] = smtplib.SMTP.connect
    originals["lmtp_connect"] = smtplib.LMTP.connect

    def blocked_smtp(self, *args, **kwargs):
        _GUARD_STATE["smtp_attempts"] += 1
        raise MailBlocked("SMTP real bloqueado en la ejecución desechable")

    smtplib.SMTP.connect = blocked_smtp
    smtplib.LMTP.connect = blocked_smtp

    try:
        import requests.sessions
    except ImportError:  # pragma: no cover - requests es dependencia del proyecto
        requests = None
    else:
        originals["requests_send"] = requests.sessions.Session.send

        def blocked_send(self, request, **kwargs):
            _GUARD_STATE["http_attempts"] += 1
            raise MailBlocked("HTTP saliente bloqueado en la ejecución desechable")

        requests.sessions.Session.send = blocked_send
    _GUARD_STATE["installed"] = True


def uninstall_mail_guards() -> None:
    """Restaura las funciones originales (uso exclusivo de pruebas unitarias)."""
    if not _GUARD_STATE["installed"]:
        return
    import smtplib

    originals = _GUARD_STATE["originals"]
    smtplib.SMTP.connect = originals.pop("smtp_connect")
    smtplib.LMTP.connect = originals.pop("lmtp_connect")
    if "requests_send" in originals:
        import requests.sessions

        requests.sessions.Session.send = originals.pop("requests_send")
    _GUARD_STATE["installed"] = False


def mail_guard_report() -> dict:
    """Recuento de intentos bloqueados, sin destinatarios ni contenido."""
    return {
        "installed": _GUARD_STATE["installed"],
        "smtp_attempts": _GUARD_STATE["smtp_attempts"],
        "http_attempts": _GUARD_STATE["http_attempts"],
    }


def reset_mail_guard_counters() -> None:
    """Reinicia los contadores (uso exclusivo de pruebas unitarias)."""
    _GUARD_STATE["smtp_attempts"] = 0
    _GUARD_STATE["http_attempts"] = 0


# ---------------------------------------------------------------------------
# Ejecución con la conexión real.
# ---------------------------------------------------------------------------


def expected_identity(settings_module: str, run_id: str) -> ExpectedIdentity:
    """Deriva la identidad esperada de las constantes de la guarda existente."""
    import config.settings_test_disposable as guard

    return ExpectedIdentity(
        settings_module=settings_module,
        run_id=run_id,
        database=guard.disposable_database_name(run_id),
        user=guard.EXPECTED_TEST_DB_USER,
        host=guard.DEFAULT_TEST_DB_HOST,
        port=guard.DEFAULT_TEST_DB_PORT,
        server_port=guard.EXPECTED_SERVER_PORT,
        hostname=guard.disposable_container_hostname(run_id),
        protected=frozenset(guard.PROTECTED_DATABASES) | EXPLICIT_PROTECTED_DATABASES,
    )


def _fetch_identity(connection):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT DATABASE(), CURRENT_USER(), @@port, @@hostname, @@version, "
            "@@default_storage_engine"
        )
        identity = cursor.fetchone()
        cursor.execute("SHOW GRANTS FOR CURRENT_USER()")
        grants = [row[0] for row in cursor.fetchall()]
        cursor.execute("SELECT CURRENT_ROLE()")
        role_row = cursor.fetchone()
        cursor.execute("SELECT COUNT(*) FROM information_schema.APPLICABLE_ROLES")
        roles = int(cursor.fetchone()[0])
        cursor.execute(
            "SELECT TABLE_NAME FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE()"
        )
        tables = [row[0] for row in cursor.fetchall()]
    return identity, grants, (role_row[0] if role_row else None), roles, tables


def _schema_facts(connection, mode: str):
    if mode != SCHEMA_MIGRATED:
        return frozenset(), 0
    from django.db.migrations.executor import MigrationExecutor

    executor = MigrationExecutor(connection)
    pending = len(executor.migration_plan(executor.loader.graph.leaf_nodes()))
    known = frozenset(
        connection.introspection.django_table_names(only_existing=False, include_views=True)
    )
    return known, pending


def run_preflight(
    *,
    connection,
    databases: dict,
    expected: ExpectedIdentity,
    mode: str,
    environ,
    mail_settings: dict,
    guards_installed: bool,
    static_validator=None,
    live_validator=None,
    isolated_validators=None,
    schema_facts=None,
    redis_validator=None,
) -> PreflightResult:
    """Ejecuta todas las comprobaciones en orden y devuelve el primer rechazo.

    Orden: módulo, atributos de conexión, validador estático existente,
    conexión, validador en vivo existente, identidad del servidor, privilegios,
    esquema, bloqueo de correo y, solo con Redis desechable (F10.3.4-A), la
    caché y la identidad de Redis.
    """
    from django.core.exceptions import ImproperlyConfigured
    from django.db.utils import OperationalError

    result = PreflightResult()

    def passed(name):
        result.checks.append({"check": name, "ok": True})

    try:
        check_settings_module(
            expected.settings_module, environ.get("DJANGO_SETTINGS_MODULE")
        )
        if environ.get("AGROTECH_DISPOSABLE_RUN_ID") != expected.run_id:
            raise PreflightRejected(
                STATUS_CONFIG_NOT_ALLOWED,
                "el identificador de ejecución del entorno no coincide",
            )
        passed("settings_module")
        check_connection_settings(databases, expected)
        passed("connection_settings")
        if static_validator is not None:
            try:
                static_validator(expected.settings_module, databases)
            except ImproperlyConfigured as exc:
                raise PreflightRejected(STATUS_CONFIG_NOT_ALLOWED, str(exc)) from None
        passed("static_validator")
        try:
            connection.ensure_connection()
        except OperationalError:
            raise PreflightRejected(
                STATUS_CONNECTION_ERROR, "no se pudo conectar con la base esperada"
            ) from None
        passed("connection")
        if live_validator is not None:
            try:
                live_validator(connection)
            except ImproperlyConfigured as exc:
                raise PreflightRejected(STATUS_SERVER_IDENTITY_MISMATCH, str(exc)) from None
        passed("live_validator")
        identity, grants, role, roles, tables = _fetch_identity(connection)
        check_server_identity(identity, expected)
        passed("server_identity")
        check_privileges(grants, role, roles, expected.database)
        passed("privileges")
        if isolated_validators is not None:
            try:
                isolated_validators(connection, mode)
            except ImproperlyConfigured as exc:
                raise PreflightRejected(STATUS_UNEXPECTED_SCHEMA, str(exc)) from None
        facts = schema_facts or _schema_facts
        known, pending = facts(connection, mode)
        check_schema(tables, mode, known, pending)
        passed("schema")
        check_mail_blocked(environ, mail_settings, guards_installed)
        passed("mail_blocked")
        if redis_validator is not None:
            try:
                redis_validator()
            except ImproperlyConfigured as exc:
                raise PreflightRejected(STATUS_REDIS_NOT_ALLOWED, str(exc)) from None
            passed("redis")
    except PreflightRejected as exc:
        result.status = exc.status
        result.detail = exc.detail
    except OperationalError:
        result.status = STATUS_CONNECTION_ERROR
        result.detail = "error de conexión durante el preflight"
    except Exception as exc:  # noqa: BLE001 - se informa como error interno
        result.status = STATUS_INTERNAL_ERROR
        result.detail = f"error interno de validación ({type(exc).__name__})"
    return result


def _isolated_validators(connection, mode: str) -> None:
    from config.settings_test_disposable_isolated import (
        validate_isolated_applied_migrations,
        validate_isolated_migrations,
        validate_isolated_tables,
    )

    validate_isolated_migrations()
    if mode == SCHEMA_MIGRATED:
        validate_isolated_applied_migrations(connection)
        validate_isolated_tables(connection)


def check_disposable_redis() -> None:
    """Caché configurada y Redis real de la ejecución (F10.3.4-A).

    Lanza ``ImproperlyConfigured`` si la caché no es exactamente el Redis
    desechable o si el servidor conectado no demuestra ser el de este RunId.
    """
    from django.conf import settings

    import config.settings_test_disposable as guard

    guard.validate_disposable_cache(
        settings.CACHES, getattr(settings, "DJANGO_REDIS_IGNORE_EXCEPTIONS", False)
    )
    guard.validate_live_disposable_redis()


def _write_report(report_dir: Path, name: str, payload: dict) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    target = report_dir / name
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, target)


def _classify_import_error(message: str) -> str:
    return STATUS_PROTECTED_DATABASE if "protegida" in message else STATUS_CONFIG_NOT_ALLOWED


def _configure_django(settings_module: str) -> dict:
    """Carga la configuración autorizada, fuerza el backend local y arranca Django."""
    import django
    from django.conf import settings

    settings.EMAIL_BACKEND = LOCMEM_EMAIL_BACKEND
    django.setup()
    return {
        "EMAIL_BACKEND": settings.EMAIL_BACKEND,
        "EMAIL_HOST": settings.EMAIL_HOST,
        "EMAIL_HOST_USER": settings.EMAIL_HOST_USER,
        "DEFAULT_FROM_EMAIL": settings.DEFAULT_FROM_EMAIL,
        "SETTINGS_MODULE": settings.SETTINGS_MODULE,
    }


def _live_preflight(settings_module: str, run_id: str, mode: str) -> PreflightResult:
    from django.conf import settings
    from django.db import connection

    import config.settings_test_disposable as base_guard

    guard = base_guard
    if settings_module == ISOLATED_SETTINGS_MODULE:
        import config.settings_test_disposable_isolated as guard
    mail_settings = {
        "EMAIL_BACKEND": settings.EMAIL_BACKEND,
        "EMAIL_HOST": settings.EMAIL_HOST,
        "EMAIL_HOST_USER": settings.EMAIL_HOST_USER,
        "DEFAULT_FROM_EMAIL": settings.DEFAULT_FROM_EMAIL,
    }
    if settings.SETTINGS_MODULE != settings_module:
        return PreflightResult(
            STATUS_CONFIG_NOT_ALLOWED, "la configuración cargada no coincide"
        )
    return run_preflight(
        connection=connection,
        databases=settings.DATABASES,
        expected=expected_identity(settings_module, run_id),
        mode=mode,
        environ=os.environ,
        mail_settings=mail_settings,
        guards_installed=mail_guard_report()["installed"],
        static_validator=guard.validate_disposable_environment,
        live_validator=base_guard.validate_live_disposable_database,
        isolated_validators=(
            _isolated_validators if settings_module == ISOLATED_SETTINGS_MODULE else None
        ),
        redis_validator=(
            check_disposable_redis if base_guard.disposable_redis_enabled() else None
        ),
    )


def main(argv=None) -> int:
    """Punto de entrada: preflight, migrate o sqlmigrate (siempre tras preflight)."""
    parser = argparse.ArgumentParser(prog="config.disposable_preflight")
    parser.add_argument("command", choices=("preflight", "migrate", "sqlmigrate"))
    parser.add_argument("--settings-module", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--expect-schema", choices=(SCHEMA_EMPTY, SCHEMA_MIGRATED), default=SCHEMA_EMPTY
    )
    args = parser.parse_args(argv)

    base_dir = Path(__file__).resolve().parent.parent
    report = {"command": args.command, "run_id": args.run_id}
    try:
        report_dir = check_report_dir(os.environ.get(REPORT_DIR_ENV), base_dir)
    except PreflightRejected as exc:
        print(f"preflight: {exc.status}: {exc.detail}", file=sys.stderr)
        return EXIT_CODES["PREFLIGHT_REJECTED"]
    report_name = f"preflight-{args.command}-{args.expect_schema}.json"

    def finish(code: int, payload: dict) -> int:
        payload.update(report)
        payload["exit_code"] = code
        payload["mail_guard"] = mail_guard_report()
        _write_report(report_dir, report_name, payload)
        print(f"preflight: {payload.get('status', '')} (código {code})")
        return code

    # 1. Módulo autorizado ANTES de importar la configuración de Django.
    try:
        check_settings_module(args.settings_module, os.environ.get("DJANGO_SETTINGS_MODULE"))
    except PreflightRejected as exc:
        return finish(
            EXIT_CODES["PREFLIGHT_REJECTED"], {"status": exc.status, "detail": exc.detail}
        )

    install_mail_guards()
    from django.core.exceptions import ImproperlyConfigured

    try:
        _configure_django(args.settings_module)
    except ImproperlyConfigured as exc:
        status = _classify_import_error(str(exc))
        return finish(EXIT_CODES["PREFLIGHT_REJECTED"], {"status": status, "detail": str(exc)})
    except Exception as exc:  # noqa: BLE001
        return finish(
            EXIT_CODES["INFRASTRUCTURE_FAILED"],
            {"status": STATUS_INTERNAL_ERROR, "detail": type(exc).__name__},
        )

    from django.db import connections

    try:
        mode = SCHEMA_EMPTY if args.command in ("migrate", "sqlmigrate") else args.expect_schema
        result = _live_preflight(args.settings_module, args.run_id, mode)
        payload = result.to_dict()
        if not result.ok or args.command == "preflight":
            return finish(exit_code_for_status(result.status), payload)

        # 2. Solo con PREFLIGHT_PASSED se ejecuta la operación sobre la base.
        payload["preflight_passed"] = True
        from django.core.management import call_command

        try:
            if args.command == "migrate":
                call_command("migrate", interactive=False, verbosity=1)
            else:
                call_command("sqlmigrate", "trazabilidad", "0007")
        except Exception as exc:  # noqa: BLE001
            payload["operation"] = {"ok": False, "error": type(exc).__name__}
            return finish(EXIT_CODES["MIGRATION_FAILED"], payload)
        payload["operation"] = {"ok": True}

        if args.command == "migrate":
            post = _live_preflight(args.settings_module, args.run_id, SCHEMA_MIGRATED)
            payload["post_migration"] = post.to_dict()
            if not post.ok:
                return finish(EXIT_CODES["MIGRATION_FAILED"], payload)
        return finish(EXIT_CODES["OK"], payload)
    finally:
        connections.close_all()


# ---------------------------------------------------------------------------
# Plugin de pytest: ``-p config.disposable_preflight``.
# ---------------------------------------------------------------------------


def pytest_configure(config) -> None:
    """Instala el bloqueo de correo antes de cargar las pruebas."""
    import pytest

    for name in MAIL_ENV_MUST_BE_EMPTY:
        if os.environ.get(name, "") != "":
            raise pytest.UsageError(
                f"Ejecución desechable bloqueada: variable de correo no vacía ({name})."
            )
    if str(os.environ.get(DOTENV_DISABLED_ENV, "")).casefold() not in {"1", "true", "yes"}:
        raise pytest.UsageError("Ejecución desechable bloqueada: .env no está desactivado.")
    install_mail_guards()
    _check_redis_for_pytest(pytest)


def _check_redis_for_pytest(pytest) -> None:
    """Con Redis desechable, repite su verificación antes de cargar las pruebas."""
    if os.environ.get(REDIS_ENABLED_ENV, "") == "":
        return
    from django.core.exceptions import ImproperlyConfigured

    try:
        import config.settings_test_disposable as guard

        if not guard.disposable_redis_enabled():
            return
        check_disposable_redis()
    except ImproperlyConfigured as exc:
        raise pytest.UsageError(f"Ejecución desechable bloqueada: {exc}") from None


def pytest_sessionfinish(session, exitstatus) -> None:
    """Registra el recuento de intentos de envío bloqueados fuera del repositorio."""
    report_dir = os.environ.get(REPORT_DIR_ENV)
    if not report_dir:
        return
    try:
        target = check_report_dir(report_dir, Path(__file__).resolve().parent.parent)
    except PreflightRejected:
        return
    payload = dict(mail_guard_report())
    payload["pytest_exitstatus"] = int(exitstatus)
    _write_report(target, "mailguard-pytest.json", payload)


if __name__ == "__main__":  # pragma: no cover - lo invoca el orquestador
    sys.exit(main())
