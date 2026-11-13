"""Pruebas unitarias de ``config.disposable_preflight``.

No se conectan a MariaDB, no ejecutan migraciones y no abren conexiones SMTP
ni HTTP: usan conexiones falsas y el bloqueo de correo del propio módulo.
"""
import importlib
import json
import re
import smtplib
from pathlib import Path

import pytest
from django.db.utils import OperationalError

from config import disposable_preflight as pf

RUN_ID = "0123456789ab"
DATABASE = f"agrotech_permtest_{RUN_ID}"
HOSTNAME = f"agrotech-permtest-{RUN_ID}"
REPO_ROOT = Path(__file__).resolve().parent.parent

USER = "`agrotech_perm_validation`@`%`"
# Destino con los guiones bajos escapados, tal como lo concede la imagen.
DB_TARGET = "`agrotech\\_permtest\\_0123456789ab`.*"
GOOD_GRANTS = [
    f"GRANT USAGE ON *.* TO {USER} IDENTIFIED BY PASSWORD '*HASH'",
    f"GRANT ALL PRIVILEGES ON `agrotech\\_permtest\\_0123456789ab`.* TO {USER}",
]
EXCESSIVE = "EXCESSIVE_PRIVILEGES"
INSUFFICIENT = "INSUFFICIENT_PRIVILEGES"


def _expected(**overrides):
    values = dict(
        settings_module="config.settings_test_disposable",
        run_id=RUN_ID,
        database=DATABASE,
        user="agrotech_perm_validation",
        host="127.0.0.1",
        port="3317",
        server_port="3306",
        hostname=HOSTNAME,
        protected=frozenset({"agrotech_db", "agrotech_t", "agrotech_farms_validation"}),
    )
    values.update(overrides)
    return pf.ExpectedIdentity(**values)


def _databases(**overrides):
    default = {
        "ENGINE": "django.db.backends.mysql",
        "NAME": DATABASE,
        "USER": "agrotech_perm_validation",
        "HOST": "127.0.0.1",
        "PORT": "3317",
        "TEST": {"NAME": DATABASE},
    }
    default.update(overrides)
    return {"default": default}


def _identity_row(**overrides):
    row = {
        "database": DATABASE,
        "user": "agrotech_perm_validation@%",
        "port": 3306,
        "hostname": HOSTNAME,
        "version": "11.8.8-MariaDB-ubu2404",
        "engine": "InnoDB",
    }
    row.update(overrides)
    keys = ("database", "user", "port", "hostname", "version", "engine")
    return tuple(row[key] for key in keys)


def _mail_environ(**overrides):
    environ = {name: "" for name in pf.MAIL_ENV_MUST_BE_EMPTY}
    environ.update(
        {
            "PYTHON_DOTENV_DISABLED": "1",
            "DJANGO_SETTINGS_MODULE": "config.settings_test_disposable",
            "AGROTECH_DISPOSABLE_RUN_ID": RUN_ID,
        }
    )
    environ.update(overrides)
    return environ


def _mail_settings(**overrides):
    values = {
        "EMAIL_BACKEND": pf.LOCMEM_EMAIL_BACKEND,
        "EMAIL_HOST": "",
        "EMAIL_HOST_USER": "",
        "DEFAULT_FROM_EMAIL": "",
    }
    values.update(overrides)
    return values


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        text = " ".join(sql.split()).upper()
        self.conn.queries.append(text)
        if text.startswith("SELECT DATABASE()"):
            self.rows = [self.conn.identity]
        elif text.startswith("SHOW GRANTS"):
            self.rows = [(line,) for line in self.conn.grants]
        elif "CURRENT_ROLE()" in text:
            self.rows = [(self.conn.role,)]
        elif "APPLICABLE_ROLES" in text:
            self.rows = [(self.conn.roles,)]
        elif "INFORMATION_SCHEMA.TABLES" in text:
            self.rows = [(name,) for name in self.conn.tables]
        else:  # pragma: no cover - consulta inesperada
            raise AssertionError(f"consulta inesperada: {text}")

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class FakeConnection:
    def __init__(self, **overrides):
        self.identity = _identity_row()
        self.grants = list(GOOD_GRANTS)
        self.role = None
        self.roles = 0
        self.tables = []
        self.fail_connect = False
        self.queries = []
        for key, value in overrides.items():
            setattr(self, key, value)

    def ensure_connection(self):
        if self.fail_connect:
            raise OperationalError(2003, "no se pudo conectar")

    def cursor(self):
        return FakeCursor(self)


def _run(connection=None, *, databases=None, expected=None, mode=pf.SCHEMA_EMPTY,
         environ=None, mail_settings=None, guards=True, **kwargs):
    return pf.run_preflight(
        connection=connection or FakeConnection(),
        databases=databases or _databases(),
        expected=expected or _expected(),
        mode=mode,
        environ=environ or _mail_environ(),
        mail_settings=mail_settings or _mail_settings(),
        guards_installed=guards,
        schema_facts=kwargs.pop("schema_facts", lambda conn, m: (frozenset(), 0)),
        **kwargs,
    )


@pytest.fixture(autouse=True)
def _restore_guards():
    yield
    pf.uninstall_mail_guards()
    pf.reset_mail_guard_counters()


# --- 8.2 Configuración ------------------------------------------------------


@pytest.mark.parametrize("module", pf.ALLOWED_SETTINGS_MODULES)
def test_allowed_settings_module_accepted(module):
    pf.check_settings_module(module, module)


@pytest.mark.parametrize(
    "module", ["config.settings", "config.settings_test", "config.otro", "", None]
)
def test_not_allowed_or_missing_settings_module_rejected(module):
    with pytest.raises(pf.PreflightRejected) as exc:
        pf.check_settings_module(module, module)
    assert exc.value.status == pf.STATUS_CONFIG_NOT_ALLOWED


def test_settings_module_must_match_environment():
    with pytest.raises(pf.PreflightRejected) as exc:
        pf.check_settings_module("config.settings_test_disposable", "config.settings")
    assert exc.value.status == pf.STATUS_CONFIG_NOT_ALLOWED


def test_allowlisted_modules_exist_in_repository():
    for module in pf.ALLOWED_SETTINGS_MODULES:
        assert (REPO_ROOT / (module.replace(".", "/") + ".py")).is_file()


# --- 8.3 Preflight ----------------------------------------------------------


def test_valid_preflight_passes_all_checks():
    result = _run()
    assert result.ok, result.detail
    names = [item["check"] for item in result.checks]
    assert names == [
        "settings_module", "connection_settings", "static_validator", "connection",
        "live_validator", "server_identity", "privileges", "schema", "mail_blocked",
    ]
    assert result.to_dict()["exit_code"] == 0


@pytest.mark.parametrize("name", ["agrotech_db", "agrotech_t", "AGROTECH_T"])
def test_protected_database_in_settings_rejected(name):
    result = _run(databases=_databases(NAME=name, TEST={"NAME": name}))
    assert result.status == pf.STATUS_PROTECTED_DATABASE
    assert result.to_dict()["exit_code"] == pf.EXIT_CODES["PREFLIGHT_REJECTED"]


@pytest.mark.parametrize("name", ["agrotech_db", "agrotech_t"])
def test_protected_database_on_connected_server_rejected(name):
    conn = FakeConnection(identity=_identity_row(database=name))
    assert _run(conn).status == pf.STATUS_PROTECTED_DATABASE


@pytest.mark.parametrize(
    "overrides",
    [{"HOST": "localhost"}, {"HOST": "10.0.0.5"}, {"PORT": "3306"}, {"PORT": "3307"},
     {"NAME": "agrotech_permtest_ffffffffffff", "TEST": {"NAME": "agrotech_permtest_ffffffffffff"}},
     {"USER": "root"}, {"ENGINE": "django.db.backends.sqlite3"}],
)
def test_wrong_connection_attributes_rejected(overrides):
    assert _run(databases=_databases(**overrides)).status == pf.STATUS_CONFIG_NOT_ALLOWED


def test_extra_database_alias_rejected():
    databases = _databases()
    databases["other"] = dict(databases["default"])
    assert _run(databases=databases).status == pf.STATUS_CONFIG_NOT_ALLOWED


def test_run_id_mismatch_in_environment_rejected():
    environ = _mail_environ(AGROTECH_DISPOSABLE_RUN_ID="ffffffffffff")
    assert _run(environ=environ).status == pf.STATUS_CONFIG_NOT_ALLOWED


@pytest.mark.parametrize(
    "overrides",
    [{"hostname": "otro-servidor"}, {"port": 3307}, {"database": "agrotech_permtest_ffffffffffff"},
     {"user": "root@localhost"}, {"engine": "MyISAM"}, {"version": "10.11.6-MariaDB"},
     {"version": "8.0.36"}],
)
def test_wrong_server_identity_rejected(overrides):
    conn = FakeConnection(identity=_identity_row(**overrides))
    assert _run(conn).status == pf.STATUS_SERVER_IDENTITY_MISMATCH


def test_connection_error_reported():
    result = _run(FakeConnection(fail_connect=True))
    assert result.status == pf.STATUS_CONNECTION_ERROR
    assert result.to_dict()["exit_code"] == pf.EXIT_CODES["PREFLIGHT_REJECTED"]


def test_internal_error_reported_without_details():
    def broken(connection):
        raise RuntimeError("secreto-que-no-debe-salir")

    result = _run(live_validator=broken)
    assert result.status == pf.STATUS_INTERNAL_ERROR
    assert "secreto" not in result.detail
    assert result.to_dict()["exit_code"] == pf.EXIT_CODES["INFRASTRUCTURE_FAILED"]


def test_existing_live_validator_rejection_mapped():
    from django.core.exceptions import ImproperlyConfigured

    def reject(connection):
        raise ImproperlyConfigured("la conexión no apunta al contenedor autorizado")

    assert _run(live_validator=reject).status == pf.STATUS_SERVER_IDENTITY_MISMATCH


def test_existing_static_validator_rejection_mapped():
    from django.core.exceptions import ImproperlyConfigured

    def reject(module, databases):
        raise ImproperlyConfigured("nombre de base no autorizado")

    assert _run(static_validator=reject).status == pf.STATUS_CONFIG_NOT_ALLOWED


@pytest.mark.parametrize(
    "grants, role, roles, status",
    [
        ([f"GRANT SELECT ON *.* TO {USER}"] + GOOD_GRANTS[1:], None, 0, EXCESSIVE),
        ([f"GRANT ALL PRIVILEGES ON *.* TO {USER}"], None, 0, EXCESSIVE),
        (
            GOOD_GRANTS[:1] + [f"GRANT ALL PRIVILEGES ON {DB_TARGET} TO {USER} WITH GRANT OPTION"],
            None, 0, EXCESSIVE,
        ),
        (GOOD_GRANTS + [f"GRANT SELECT ON `agrotech_t`.* TO {USER}"], None, 0, EXCESSIVE),
        (GOOD_GRANTS + [f"GRANT `rol_admin` TO {USER}"], None, 0, EXCESSIVE),
        (GOOD_GRANTS, "rol_admin", 0, EXCESSIVE),
        (GOOD_GRANTS, None, 1, EXCESSIVE),
        (
            GOOD_GRANTS[:1] + [f"GRANT SELECT, INSERT ON {DB_TARGET} TO {USER}"],
            None, 0, INSUFFICIENT,
        ),
        (GOOD_GRANTS[:1], None, 0, INSUFFICIENT),
    ],
)
def test_privileges_rejected(grants, role, roles, status):
    conn = FakeConnection(grants=grants, role=role, roles=roles)
    assert _run(conn).status == status


def test_explicit_privilege_list_accepted():
    grants = GOOD_GRANTS[:1] + [
        "GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, REFERENCES, INDEX, ALTER "
        f"ON {DB_TARGET} TO {USER}"
    ]
    assert _run(FakeConnection(grants=grants)).ok


# --- AUD-003/AUD-004: destino de SHOW GRANTS ---------------------------------


@pytest.mark.parametrize(
    "target",
    [
        "`agrotech\\_permtest\\_0123456789ab`.*",  # \_ (escape de MariaDB)
        "`agrotech\\\\_permtest\\\\_0123456789ab`.*",  # \\_ (mariadb --batch sin --raw)
        "`agrotech\\_permtest\\_0123456789ab`.`farms`",  # nivel de tabla
    ],
)
def test_decode_grant_schema_accepts_escaped_names(target):
    assert pf.decode_grant_schema(target) == DATABASE


@pytest.mark.parametrize(
    "target",
    [
        "`agrotech_permtest_0123456789ab`.*",  # _ sin escapar: comodín
        "`agrotech\\_permtest\\_%`.*",  # % sin escapar: comodín
        "`agrotech\\_permtest\\_0123\\a`.*",  # escape desconocido
        "agrotech\\_permtest\\_0123456789ab.*",  # sin comillas invertidas
        "`agrotech\\_permtest\\_0123456789ab`",  # sin .*
        "*.*",
        "FUNCTION",
        "",
    ],
)
def test_decode_grant_schema_rejects_wildcards_and_unknown_formats(target):
    with pytest.raises(pf.PreflightRejected) as exc:
        pf.decode_grant_schema(target)
    assert exc.value.status == pf.STATUS_EXCESSIVE_PRIVILEGES


def test_unescaped_wildcard_grant_rejected_by_preflight():
    unescaped = "`agrotech_permtest_0123456789ab`.*"
    grants = GOOD_GRANTS[:1] + [f"GRANT ALL PRIVILEGES ON {unescaped} TO {USER}"]
    assert _run(FakeConnection(grants=grants)).status == pf.STATUS_EXCESSIVE_PRIVILEGES


def test_doubled_backslash_grant_accepted_by_preflight():
    grants = GOOD_GRANTS[:1] + [
        f"GRANT ALL PRIVILEGES ON `agrotech\\\\_permtest\\\\_0123456789ab`.* TO {USER}"
    ]
    assert _run(FakeConnection(grants=grants)).ok


@pytest.mark.parametrize(
    "line",
    [
        f"SET DEFAULT ROLE `rol` FOR {USER}",
        f"GRANT EXECUTE ON FUNCTION `agrotech\\_permtest\\_0123456789ab`.`f` TO {USER}",
        f"GRANT PROXY ON ''@'%' TO {USER}",
        "linea arbitraria",
    ],
)
def test_unknown_or_special_grant_lines_rejected(line):
    result = _run(FakeConnection(grants=GOOD_GRANTS + [line]))
    assert result.status == pf.STATUS_EXCESSIVE_PRIVILEGES


def test_privilege_check_never_reports_grant_lines():
    conn = FakeConnection(
        grants=[f"GRANT ALL PRIVILEGES ON *.* TO {USER} IDENTIFIED BY PASSWORD '*ABCDEF'"]
    )
    result = _run(conn)
    assert "*ABCDEF" not in json.dumps(result.to_dict())


def test_unexpected_tables_before_migrate_rejected():
    result = _run(FakeConnection(tables=["farms"]))
    assert result.status == pf.STATUS_UNEXPECTED_SCHEMA


def test_migrated_schema_requires_no_pending_migrations():
    conn = FakeConnection(tables=["django_migrations", "farms"])
    pending = _run(
        conn, mode=pf.SCHEMA_MIGRATED, schema_facts=lambda c, m: (frozenset({"farms"}), 2)
    )
    assert pending.status == pf.STATUS_UNEXPECTED_SCHEMA
    unexpected = _run(
        FakeConnection(tables=["django_migrations", "farms", "intrusa"]),
        mode=pf.SCHEMA_MIGRATED,
        schema_facts=lambda c, m: (frozenset({"farms"}), 0),
    )
    assert unexpected.status == pf.STATUS_UNEXPECTED_SCHEMA
    ok = _run(conn, mode=pf.SCHEMA_MIGRATED, schema_facts=lambda c, m: (frozenset({"farms"}), 0))
    assert ok.ok


def test_migrated_schema_without_migrations_table_rejected():
    result = _run(FakeConnection(tables=["farms"]), mode=pf.SCHEMA_MIGRATED,
                  schema_facts=lambda c, m: (frozenset({"farms"}), 0))
    assert result.status == pf.STATUS_UNEXPECTED_SCHEMA


def test_isolated_validator_rejection_mapped_to_schema():
    from django.core.exceptions import ImproperlyConfigured

    def reject(connection, mode):
        raise ImproperlyConfigured("grafo inválido")

    assert _run(isolated_validators=reject).status == pf.STATUS_UNEXPECTED_SCHEMA


def test_expected_identity_derives_from_existing_guard(monkeypatch):
    monkeypatch.setenv("AGROTECH_DISPOSABLE_CONFIRM", "1")
    monkeypatch.setenv("AGROTECH_DISPOSABLE_RUN_ID", RUN_ID)
    monkeypatch.setenv("AGROTECH_DISPOSABLE_DB_PASSWORD", "valor-de-prueba")
    import config.settings_test_disposable as guard

    importlib.reload(guard)
    expected = pf.expected_identity("config.settings_test_disposable", RUN_ID)
    assert expected.database == DATABASE
    assert expected.hostname == HOSTNAME
    assert expected.host == "127.0.0.1" and expected.port == "3317"
    assert expected.server_port == "3306"
    assert expected.user == "agrotech_perm_validation"
    assert {"agrotech_db", "agrotech_t"} <= expected.protected


# --- 8.6 Correo y .env ------------------------------------------------------


@pytest.mark.parametrize("name", pf.MAIL_ENV_MUST_BE_EMPTY)
def test_inherited_mail_variable_rejected(name):
    result = _run(environ=_mail_environ(**{name: "valor"}))
    assert result.status == pf.STATUS_MAIL_NOT_BLOCKED


def test_dotenv_not_disabled_rejected():
    result = _run(environ=_mail_environ(PYTHON_DOTENV_DISABLED=""))
    assert result.status == pf.STATUS_MAIL_NOT_BLOCKED


@pytest.mark.parametrize(
    "overrides",
    [{"EMAIL_HOST_USER": "usuario-cargado-desde-env"}, {"EMAIL_HOST": "smtp-relay.brevo.com"},
     {"DEFAULT_FROM_EMAIL": "remitente@example.com"},
     {"EMAIL_BACKEND": "django.core.mail.backends.smtp.EmailBackend"}],
)
def test_effective_mail_settings_reactivated_rejected(overrides):
    result = _run(mail_settings=_mail_settings(**overrides))
    assert result.status == pf.STATUS_MAIL_NOT_BLOCKED


def test_missing_guards_rejected():
    assert _run(guards=False).status == pf.STATUS_MAIL_NOT_BLOCKED


def test_dotenv_disabled_does_not_load_smtp(tmp_path, monkeypatch):
    import dotenv

    env_file = tmp_path / ".env"
    env_file.write_text("SMTP_USER=usuario-real\nSMTP_FROM=real@example.com\n", encoding="utf-8")
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.delenv("SMTP_FROM", raising=False)
    assert dotenv.load_dotenv(env_file) is False
    import os

    assert "SMTP_USER" not in os.environ
    assert "SMTP_FROM" not in os.environ


def test_preset_empty_variables_survive_dotenv(tmp_path, monkeypatch):
    import os

    import dotenv

    env_file = tmp_path / ".env"
    env_file.write_text("SMTP_USER=usuario-real\n", encoding="utf-8")
    monkeypatch.delenv("PYTHON_DOTENV_DISABLED", raising=False)
    monkeypatch.setenv("SMTP_USER", "")
    dotenv.load_dotenv(env_file)
    assert os.environ["SMTP_USER"] == ""


def test_smtp_connection_blocked_without_network():
    pf.install_mail_guards()
    with pytest.raises(pf.MailBlocked):
        smtplib.SMTP("127.0.0.1", 1)
    assert pf.mail_guard_report()["smtp_attempts"] == 1


def test_django_smtp_backend_blocked():
    from django.core.mail.backends.smtp import EmailBackend

    pf.install_mail_guards()
    backend = EmailBackend(host="127.0.0.1", port=1, fail_silently=False)
    with pytest.raises(pf.MailBlocked):
        backend.open()
    assert pf.mail_guard_report()["smtp_attempts"] == 1


def test_http_blocked_without_any_network_access():
    """AUD-020: ni siquiera con el bloqueo roto puede salir tráfico.

    El host usa el TLD reservado ``.invalid`` (RFC 2606) y la sesión monta un
    adaptador local que falla si se le llega a invocar: si el bloqueo no
    interceptara ``Session.send``, la petición terminaría en ese adaptador,
    sin DNS ni conexión.
    """
    import requests
    from requests.adapters import BaseAdapter

    class FailingAdapter(BaseAdapter):
        calls = 0

        def send(self, request, **kwargs):
            FailingAdapter.calls += 1
            raise AssertionError("el bloqueo HTTP no interceptó la petición")

        def close(self):
            return None

    pf.install_mail_guards()
    session = requests.Session()
    session.mount("https://", FailingAdapter())
    session.mount("http://", FailingAdapter())
    with pytest.raises(pf.MailBlocked):
        session.get("https://agrotech-blocked.invalid/v3/smtp/email", timeout=1)
    assert pf.mail_guard_report()["http_attempts"] == 1
    assert FailingAdapter.calls == 0


def test_failing_adapter_is_the_only_transport_without_guard():
    """Control: sin bloqueo, la petición llega al adaptador local, no a la red."""
    import requests
    from requests.adapters import BaseAdapter

    class RecordingAdapter(BaseAdapter):
        calls = 0

        def send(self, request, **kwargs):
            RecordingAdapter.calls += 1
            raise ConnectionError("adaptador local: sin red")

        def close(self):
            return None

    session = requests.Session()
    session.mount("https://", RecordingAdapter())
    with pytest.raises(ConnectionError):
        session.get("https://agrotech-blocked.invalid/x", timeout=1)
    assert RecordingAdapter.calls == 1


def test_guards_restorable():
    original = smtplib.SMTP.connect
    pf.install_mail_guards()
    assert smtplib.SMTP.connect is not original
    pf.uninstall_mail_guards()
    assert smtplib.SMTP.connect is original


def test_pytest_plugin_rejects_inherited_mail(monkeypatch):
    monkeypatch.setenv("SMTP_USER", "x")
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    with pytest.raises(pytest.UsageError):
        pf.pytest_configure(None)


def test_pytest_plugin_rejects_enabled_dotenv(monkeypatch):
    for name in pf.MAIL_ENV_MUST_BE_EMPTY:
        monkeypatch.setenv(name, "")
    monkeypatch.delenv("PYTHON_DOTENV_DISABLED", raising=False)
    with pytest.raises(pytest.UsageError):
        pf.pytest_configure(None)


def test_pytest_plugin_installs_guards(monkeypatch):
    for name in pf.MAIL_ENV_MUST_BE_EMPTY:
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    pf.pytest_configure(None)
    assert pf.mail_guard_report()["installed"] is True


# --- Directorio de resultados ----------------------------------------------


def test_report_dir_inside_repository_rejected():
    with pytest.raises(pf.PreflightRejected):
        pf.check_report_dir(str(REPO_ROOT / "resultados"), REPO_ROOT)
    with pytest.raises(pf.PreflightRejected):
        pf.check_report_dir(str(REPO_ROOT), REPO_ROOT)
    with pytest.raises(pf.PreflightRejected):
        pf.check_report_dir("", REPO_ROOT)


def test_report_dir_outside_repository_accepted(tmp_path):
    assert pf.check_report_dir(str(tmp_path), REPO_ROOT) == tmp_path.resolve()


# --- Orden: preflight antes de migrate (main) -------------------------------


@pytest.fixture
def main_env(monkeypatch, tmp_path):
    monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "config.settings_test_disposable")
    monkeypatch.setenv(pf.REPORT_DIR_ENV, str(tmp_path))
    calls = {"configure": 0, "commands": []}

    def fake_configure(module):
        calls["configure"] += 1
        return {}

    def fake_call_command(*args, **kwargs):
        calls["commands"].append(args)

    monkeypatch.setattr(pf, "_configure_django", fake_configure)
    monkeypatch.setattr("django.core.management.call_command", fake_call_command)
    return calls, tmp_path


def _args(command, module="config.settings_test_disposable"):
    return [command, "--settings-module", module, "--run-id", RUN_ID]


def _set_results(monkeypatch, *results):
    queue = list(results)
    modes = []

    def fake_live(module, run_id, mode):
        modes.append(mode)
        return queue.pop(0)

    monkeypatch.setattr(pf, "_live_preflight", fake_live)
    return modes


def test_main_rejected_preflight_never_migrates(main_env, monkeypatch):
    calls, out = main_env
    _set_results(monkeypatch, pf.PreflightResult(pf.STATUS_PROTECTED_DATABASE, "base protegida"))
    assert pf.main(_args("migrate")) == 10
    assert calls["commands"] == []
    report = json.loads((out / "preflight-migrate-empty.json").read_text(encoding="utf-8"))
    assert report["status"] == pf.STATUS_PROTECTED_DATABASE


def test_main_migrates_only_after_preflight_and_verifies(main_env, monkeypatch):
    calls, out = main_env
    modes = _set_results(monkeypatch, pf.PreflightResult(), pf.PreflightResult())
    assert pf.main(_args("migrate")) == 0
    assert calls["commands"] == [("migrate",)]
    assert modes == [pf.SCHEMA_EMPTY, pf.SCHEMA_MIGRATED]
    report = json.loads((out / "preflight-migrate-empty.json").read_text(encoding="utf-8"))
    assert report["preflight_passed"] is True
    assert report["mail_guard"]["installed"] is True


def test_main_migration_error_returns_20(main_env, monkeypatch):
    calls, _ = main_env
    _set_results(monkeypatch, pf.PreflightResult())

    def failing(*args, **kwargs):
        raise RuntimeError("fallo")

    monkeypatch.setattr("django.core.management.call_command", failing)
    assert pf.main(_args("migrate")) == 20


def test_main_post_migration_check_failure_returns_20(main_env, monkeypatch):
    rejected = pf.PreflightResult(pf.STATUS_UNEXPECTED_SCHEMA, "x")
    _set_results(monkeypatch, pf.PreflightResult(), rejected)
    assert pf.main(_args("migrate")) == 20


def test_main_sqlmigrate_after_preflight(main_env, monkeypatch):
    calls, _ = main_env
    monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "config.settings_test_disposable_isolated")
    _set_results(monkeypatch, pf.PreflightResult())
    assert pf.main(_args("sqlmigrate", "config.settings_test_disposable_isolated")) == 0
    assert calls["commands"] == [("sqlmigrate", "trazabilidad", "0007")]


def test_main_disallowed_module_never_configures_django(main_env, monkeypatch):
    calls, _ = main_env
    monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "config.settings")
    assert pf.main(_args("migrate", "config.settings")) == 10
    assert calls["configure"] == 0
    assert calls["commands"] == []


def test_main_protected_database_at_import_rejected(main_env, monkeypatch):
    from django.core.exceptions import ImproperlyConfigured

    calls, out = main_env

    def protected(module):
        raise ImproperlyConfigured("se detectó una base protegida")

    monkeypatch.setattr(pf, "_configure_django", protected)
    assert pf.main(_args("migrate")) == 10
    assert calls["commands"] == []
    report = json.loads((out / "preflight-migrate-empty.json").read_text(encoding="utf-8"))
    assert report["status"] == pf.STATUS_PROTECTED_DATABASE


def test_main_report_dir_inside_repository_rejected(main_env, monkeypatch):
    calls, _ = main_env
    monkeypatch.setenv(pf.REPORT_DIR_ENV, str(REPO_ROOT / "resultados"))
    assert pf.main(_args("migrate")) == 10
    assert calls["configure"] == 0
    assert not (REPO_ROOT / "resultados").exists()


# --- 7. Tabla de códigos coherente con el orquestador -----------------------


def test_exit_codes_match_orchestrator_table():
    lib = (REPO_ROOT / "tools" / "disposable_orchestrator_lib.ps1").read_text(encoding="utf-8-sig")
    table = dict(re.findall(r"^\s+([A-Z_]+)\s+=\s+(\d+)\s*$", lib, flags=re.MULTILINE))
    for name, value in pf.EXIT_CODES.items():
        assert int(table[name]) == value
