# ruff: noqa: S101
"""Pruebas de F10.2-CORRECCIONES: unicidad del correo bajo concurrencia.

Propósito: dos altas o cambios simultáneos al mismo correo producen una sola
cuenta con ese correo; los conflictos de integridad se traducen sin revelar
datos de otra cuenta; el bloqueo se libera siempre.
Contexto: ``auth_service.email_lock`` usa GET_LOCK de MariaDB. Es protección
de aplicación, no una restricción de la base de datos (pendiente de migración
autorizada). Se ejecuta solo en la base desechable MariaDB.
"""
import hashlib
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Thread

import pytest
from django.contrib.auth.hashers import make_password
from django.db import IntegrityError, connection, connections
from rest_framework.test import APIClient

from apps.accounts.errors import AppError
from apps.accounts.models import User
from apps.accounts.services import auth_service
from apps.accounts.tests.constants import CREDENTIAL_FIELD, REGISTER_CREDENTIAL, VALID_CREDENTIAL

pytestmark = pytest.mark.django_db


def _data(identification, correo):
    return {
        "first_name": "Luisa",
        "last_name": "Gomez",
        "identification": identification,
        "phone": "3007654321",
        "correo": correo,
        "password": REGISTER_CREDENTIAL,
    }


def _account(identification, correo):
    return User.objects.create(
        first_name="Ana",
        last_name="Pérez",
        identification=identification,
        phone="3001234567",
        correo=correo,
        password_hash=make_password(VALID_CREDENTIAL),
    )


def _lock_name(correo):
    digest = hashlib.sha256(correo.encode()).hexdigest()[:40]
    return auth_service.EMAIL_LOCK_PREFIX + digest


def _lock_is_free(correo):
    with connection.cursor() as cursor:
        cursor.execute("SELECT IS_FREE_LOCK(%s)", [_lock_name(correo)])
        return cursor.fetchone()[0] == 1


def _run_concurrently(*operations):
    barrier = Barrier(len(operations))

    def run(operation):
        barrier.wait(timeout=5)
        try:
            return operation()
        except AppError as exc:
            return exc
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=len(operations)) as pool:
        return list(pool.map(run, operations))


def test_runs_on_mariadb():
    """Las garantías de bloqueo solo valen en MariaDB/MySQL."""
    assert connection.vendor == "mysql"


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("variants", [("par@example.com", "par@example.com"),
                                      ("par@example.com", "PAR@Example.com")])
def test_concurrent_registrations_create_one_account(variants):
    first, second = variants
    results = _run_concurrently(
        lambda: auth_service.register(_data("70000001", first.strip().lower())),
        lambda: auth_service.register(_data("70000002", second.strip().lower())),
    )
    errors = [r for r in results if isinstance(r, AppError)]
    assert len(errors) == 1
    assert errors[0].status_code == 409
    assert errors[0].message == auth_service.EMAIL_TAKEN
    assert User.objects.filter(correo="par@example.com").count() == 1
    assert _lock_is_free("par@example.com")


@pytest.mark.django_db(transaction=True)
def test_concurrent_profile_changes_to_same_email_keep_one_owner():
    a = _account("70000003", "a@example.com")
    b = _account("70000004", "b@example.com")
    results = _run_concurrently(
        lambda: auth_service.update_profile(a, {"correo": "nuevo@example.com"}),
        lambda: auth_service.update_profile(b, {"correo": " NUEVO@example.com "}),
    )
    errors = [r for r in results if isinstance(r, AppError)]
    assert len(errors) == 1
    assert errors[0].message == auth_service.EMAIL_TAKEN
    assert User.objects.filter(correo="nuevo@example.com").count() == 1


@pytest.mark.django_db(transaction=True)
def test_busy_lock_returns_retryable_error(monkeypatch):
    monkeypatch.setattr(auth_service, "EMAIL_LOCK_TIMEOUT_SECONDS", 0)
    held, release = Event(), Event()

    def holder():
        try:
            with auth_service.email_lock("ocupado@example.com"):
                held.set()
                release.wait(timeout=10)
        finally:
            connections.close_all()

    thread = Thread(target=holder)
    thread.start()
    try:
        assert held.wait(timeout=5)
        with pytest.raises(AppError) as exc:
            auth_service.register(_data("70000005", "ocupado@example.com"))
        assert exc.value.status_code == 503
        assert exc.value.message == auth_service.EMAIL_BUSY
        assert not User.objects.filter(correo="ocupado@example.com").exists()
    finally:
        release.set()
        thread.join(timeout=10)
    auth_service.register(_data("70000005", "ocupado@example.com"))
    assert User.objects.filter(correo="ocupado@example.com").count() == 1


def test_lock_is_released_after_conflict(user):
    with pytest.raises(AppError):
        auth_service.register(_data("70000006", "ana@example.com"))
    assert _lock_is_free("ana@example.com")
    auth_service.register(_data("70000006", "libre@example.com"))
    assert _lock_is_free("libre@example.com")


def test_duplicate_identification_maps_to_identification_message(user):
    with pytest.raises(AppError) as exc:
        auth_service.register(_data(user.identification, "otra@example.com"))
    assert exc.value.status_code == 409
    assert exc.value.message == auth_service.IDENTIFICATION_TAKEN


def test_integrity_error_on_email_maps_to_email_message(monkeypatch):
    """Simula el conflicto que daría una futura restricción única del correo."""
    _account("70000007", "carrera@example.com")
    answers = iter([False, True])
    monkeypatch.setattr(auth_service, "email_in_use", lambda *a, **k: next(answers))

    def boom(**kwargs):
        raise IntegrityError("Duplicate entry for key 'correo'")

    monkeypatch.setattr(User.objects, "create", boom)
    with pytest.raises(AppError) as exc:
        auth_service.register(_data("70000008", "carrera@example.com"))
    assert exc.value.status_code == 409
    assert exc.value.message == auth_service.EMAIL_TAKEN


def test_unknown_integrity_error_is_generic(monkeypatch):
    monkeypatch.setattr(auth_service, "email_in_use", lambda *a, **k: False)

    def boom(**kwargs):
        raise IntegrityError("otra restricción")

    monkeypatch.setattr(User.objects, "create", boom)
    with pytest.raises(AppError) as exc:
        auth_service.register(_data("70000009", "x@example.com"))
    assert exc.value.message == auth_service.ACCOUNT_CONFLICT


def test_conflict_response_does_not_leak_other_account(user):
    body = {
        "first_name": "Luisa",
        "last_name": "Gomez",
        "identification": "70000010",
        "phone": "3007654321",
        "correo": " ANA@example.com ",
        CREDENTIAL_FIELD: REGISTER_CREDENTIAL,
    }
    response = APIClient().post("/api/v1/accounts/", body, format="json")
    assert response.status_code == 409
    assert response.json() == {"error": auth_service.EMAIL_TAKEN}
    text = response.content.decode()
    for secret in (user.identification, user.first_name, user.phone, str(user.pk)):
        assert secret not in text
