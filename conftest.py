"""Guarda de seguridad para la ejecución de pruebas.

Garantiza que pytest utilice exclusivamente ``config.settings_test`` y la base
autorizada ``agrotech_farms_test``, abortando la sesión antes de que se abra
una conexión o se aplique una migración.

La configuración se valida en ``pytest_configure``. Las opciones del runner,
las credenciales y la existencia real de la base se validan en
``pytest_fixture_setup``, antes de que pytest-django cree o reutilice la base.
Así, las pruebas estáticas no necesitan credenciales ni conexión, mientras que
cualquier prueba con acceso a base requiere ``--reuse-db``, prohíbe
``--create-db`` y exige que ``agrotech_farms_test`` ya exista.
"""
import os

import pytest
from django.conf import settings as django_settings
from django.core.exceptions import ImproperlyConfigured


def _verify_live_test_database(connection, db_blocker) -> None:
    """Ejecuta el preflight dentro del desbloqueo temporal de pytest-django."""
    from config.settings_test import validate_live_test_database

    if db_blocker is None:
        raise ImproperlyConfigured(
            "no está disponible django_db_blocker para verificar la base"
        )
    with db_blocker.unblock():
        validate_live_test_database(connection)


def pytest_configure(config: pytest.Config) -> None:
    """Aborta si la configuración efectiva no es la autorizada."""
    if not django_settings.configured:
        raise pytest.UsageError(
            "Django no está configurado; las pruebas deben usar config.settings_test."
        )

    try:
        from config.settings_test import validate_test_environment

        validate_test_environment(
            django_settings.SETTINGS_MODULE,
            django_settings.DATABASES,
        )
    except (ImproperlyConfigured, ImportError) as exc:
        raise pytest.UsageError(
            f"Ejecución bloqueada por la guarda de seguridad de pruebas: {exc}"
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

    if not os.environ.get("TEST_DB_USER") or not os.environ.get("TEST_DB_PASSWORD"):
        raise pytest.UsageError(
            "Ejecución bloqueada: TEST_DB_USER y TEST_DB_PASSWORD son "
            "obligatorios para las pruebas con base."
        )

    try:
        from config.settings_test import validate_test_credentials

        validate_test_credentials(os.environ.get("TEST_DB_USER"))
    except (ImproperlyConfigured, ImportError) as exc:
        raise pytest.UsageError(
            f"Ejecución bloqueada: {exc}"
        ) from exc

    try:
        db_blocker = request.getfixturevalue("django_db_blocker")
    except Exception as exc:
        raise pytest.UsageError(
            "Ejecución bloqueada: no se pudo obtener django_db_blocker para "
            "verificar la base en solo lectura."
        ) from exc

    from django.db import connections

    connection = connections["default"]
    try:
        _verify_live_test_database(connection, db_blocker)
    except ImproperlyConfigured as exc:
        raise pytest.UsageError(
            f"Ejecución bloqueada: {exc}"
        ) from exc
    except Exception as exc:
        raise pytest.UsageError(
            "Ejecución bloqueada: no se pudo verificar en solo lectura que "
            "agrotech_farms_test exista y sea accesible. No se creó ni modificó "
            f"ninguna base ({type(exc).__name__})."
        ) from exc
    finally:
        connection.close()
