"""Guarda de seguridad para la ejecución de pruebas.

Garantiza que pytest utilice exclusivamente una de las configuraciones de
pruebas autorizadas y una base aceptada por su propia lista blanca, abortando
la sesión antes de que se abra una conexión o se aplique una migración.

Configuraciones autorizadas (lista cerrada):

- ``config.settings_test``: base de validación ``agrotech_farms_validation``.
- ``config.settings_test_disposable``: contenedor MariaDB desechable.
- ``config.settings_test_disposable_isolated``: contenedor desechable con el
  grafo de migraciones restringido a accounts, farms y trazabilidad.

Cada configuración se valida con sus propios validadores, importados de forma
perezosa: solo se importa el módulo activo, porque cada uno valida su destino
durante la importación.

La configuración se valida en ``pytest_configure``. Las opciones del runner,
las credenciales y la identidad real de la base se validan en
``pytest_fixture_setup``, antes de que pytest-django cree o reutilice la base.
Así, las pruebas estáticas no necesitan credenciales ni conexión, mientras que
cualquier prueba con acceso a base requiere ``--reuse-db``, prohíbe
``--create-db`` y exige que la base autorizada ya exista.
"""
import os

import pytest
from django.conf import settings as django_settings
from django.core.exceptions import ImproperlyConfigured

LIVE_SETTINGS_MODULE = "config.settings_test"
DISPOSABLE_SETTINGS_MODULE = "config.settings_test_disposable"
ISOLATED_SETTINGS_MODULE = "config.settings_test_disposable_isolated"
AUTHORIZED_SETTINGS_MODULES = frozenset(
    {LIVE_SETTINGS_MODULE, DISPOSABLE_SETTINGS_MODULE, ISOLATED_SETTINGS_MODULE}
)
DISPOSABLE_SETTINGS_MODULES = frozenset(
    {DISPOSABLE_SETTINGS_MODULE, ISOLATED_SETTINGS_MODULE}
)


def _settings_guard(settings_module):
    """Importa perezosamente el módulo de validación de la configuración activa.

    Solo se importa el módulo autorizado que coincide exactamente con
    ``settings_module``; cualquier otro valor se rechaza sin importar nada.
    ``AUTHORIZED_SETTINGS_MODULES`` es la lista blanca efectiva: se consulta
    antes de cualquier importación.
    """
    if settings_module not in AUTHORIZED_SETTINGS_MODULES:
        raise ImproperlyConfigured(
            f"módulo de settings no autorizado: {settings_module!r}"
        )
    if settings_module == LIVE_SETTINGS_MODULE:
        import config.settings_test as guard
    elif settings_module == DISPOSABLE_SETTINGS_MODULE:
        import config.settings_test_disposable as guard
    elif settings_module == ISOLATED_SETTINGS_MODULE:
        import config.settings_test_disposable_isolated as guard
    else:
        raise ImproperlyConfigured(
            f"módulo de settings no autorizado: {settings_module!r}"
        )
    return guard


def _active_settings_module() -> str:
    """Retorna el módulo de settings activo de la sesión de pruebas."""
    return django_settings.SETTINGS_MODULE


def _validate_settings_environment(settings_module, databases: dict) -> None:
    """Valida el módulo de settings activo y su configuración de base."""
    guard = _settings_guard(settings_module)
    if settings_module in DISPOSABLE_SETTINGS_MODULES:
        guard.validate_disposable_environment(settings_module, databases)
        return
    guard.validate_test_environment(settings_module, databases)


def _verify_live_test_database(connection, db_blocker) -> None:
    """Ejecuta el preflight dentro del desbloqueo temporal de pytest-django."""
    from config.settings_test import validate_live_test_database

    if db_blocker is None:
        raise ImproperlyConfigured(
            "no está disponible django_db_blocker para verificar la base"
        )
    with db_blocker.unblock():
        validate_live_test_database(connection)


def _verify_live_disposable_database(connection, db_blocker) -> None:
    """Verifica la identidad del contenedor desechable en solo lectura.

    Comprueba base, usuario, puerto interno, hostname del contenedor, motor
    InnoDB y existencia del esquema dentro del desbloqueo temporal de
    pytest-django.
    """
    from config.settings_test_disposable import validate_live_disposable_database

    if db_blocker is None:
        raise ImproperlyConfigured(
            "no está disponible django_db_blocker para verificar la base"
        )
    with db_blocker.unblock():
        validate_live_disposable_database(connection)


def _validate_runner_credentials(settings_module) -> None:
    """Exige las credenciales y validaciones propias de cada configuración."""
    guard = _settings_guard(settings_module)
    if settings_module in DISPOSABLE_SETTINGS_MODULES:
        guard.validate_disposable_runtime_environment()
        if settings_module == ISOLATED_SETTINGS_MODULE:
            guard.validate_isolated_migrations()
        return
    guard.validate_test_credentials(
        os.environ.get("TEST_DB_USER"),
        os.environ.get("TEST_DB_PASSWORD"),
    )


def pytest_configure(config: pytest.Config) -> None:
    """Aborta si la configuración efectiva no es la autorizada."""
    if not django_settings.configured:
        raise pytest.UsageError(
            "Django no está configurado; las pruebas deben usar una "
            "configuración de pruebas autorizada."
        )

    try:
        _validate_settings_environment(
            django_settings.SETTINGS_MODULE,
            django_settings.DATABASES,
        )
    except (ImproperlyConfigured, ImportError) as exc:
        raise pytest.UsageError(
            f"Ejecución bloqueada por la guarda de seguridad de pruebas: {exc}"
        ) from exc
    except Exception as exc:
        raise pytest.UsageError(
            "Ejecución bloqueada por la guarda de seguridad de pruebas: falló "
            f"la validación de la configuración ({type(exc).__name__})."
        ) from exc


@pytest.hookimpl(tryfirst=True)
def pytest_fixture_setup(fixturedef, request) -> None:
    """Impide crear bases y exige credenciales antes del setup de la BD."""
    if fixturedef.argname != "django_db_setup":
        return

    if request.config.getoption("create_db"):
        raise pytest.UsageError(
            "Ejecución bloqueada: --create-db está prohibido; use --reuse-db "
            "sobre la base de pruebas ya validada."
        )

    if not request.config.getoption("reuse_db"):
        raise pytest.UsageError(
            "Ejecución bloqueada: las pruebas con base requieren --reuse-db "
            "para impedir que pytest-django elimine o recree la base."
        )

    settings_module = _active_settings_module()
    try:
        _validate_runner_credentials(settings_module)
    except (ImproperlyConfigured, ImportError) as exc:
        raise pytest.UsageError(
            f"Ejecución bloqueada: {exc}"
        ) from exc
    except Exception as exc:
        raise pytest.UsageError(
            "Ejecución bloqueada: falló la validación previa de la configuración "
            f"de pruebas ({type(exc).__name__}). No se preparó ninguna base."
        ) from exc

    try:
        db_blocker = request.getfixturevalue("django_db_blocker")
    except Exception as exc:
        raise pytest.UsageError(
            "Ejecución bloqueada: no se pudo obtener django_db_blocker para "
            "verificar la base en solo lectura."
        ) from exc

    from django.db import connections

    if settings_module in DISPOSABLE_SETTINGS_MODULES:
        verify = _verify_live_disposable_database
    else:
        verify = _verify_live_test_database

    connection = connections["default"]
    try:
        verify(connection, db_blocker)
    except ImproperlyConfigured as exc:
        raise pytest.UsageError(
            f"Ejecución bloqueada: {exc}"
        ) from exc
    except Exception as exc:
        raise pytest.UsageError(
            "Ejecución bloqueada: no se pudo verificar en solo lectura que "
            "la base autorizada exista y sea accesible. No se creó ni modificó "
            f"ninguna base ({type(exc).__name__})."
        ) from exc
    finally:
        connection.close()
