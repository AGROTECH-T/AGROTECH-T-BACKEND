# ruff: noqa: S101
"""Pruebas de F10.2-CORRECCIONES: reintentos de la creación de la primera finca.

Propósito: un reintento de la misma operación de onboarding (doble envío
simultáneo o reintento tras perder la respuesta) devuelve la finca ya creada
en lugar de duplicarla; las creaciones adicionales legítimas no se bloquean.
Contexto: la regla está en ``services._first_farm_replay`` y se apoya en el
bloqueo de la cuenta y en la auditoría de solo inserción (sin esquema nuevo).
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from django.db import connections
from rest_framework.test import APIClient

from apps.accounts.errors import AppError
from apps.accounts.services.capability_service import set_farm_creation
from apps.farms import services
from apps.farms.models import Farm, FarmMembership, FarmRole
from apps.farms.tests.conftest import VALID_PASSWORD
from apps.trazabilidad.models import AuditEvent, EventType

pytestmark = pytest.mark.django_db


def _client(account) -> APIClient:
    client = APIClient()
    response = client.post(
        "/api/v1/auth/login/",
        {"identification": account.identification, "password": VALID_PASSWORD},
        format="json",
    )
    assert response.status_code == 200
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['token']}")
    return client


def _newcomer(make_user, identification="11111111", **kwargs):
    return make_user(identification, can_create_farms=False, **kwargs)


def _created_events():
    return AuditEvent.objects.filter(event_type=EventType.FARM_CREATED)


def _grants(account):
    return AuditEvent.objects.filter(
        event_type=EventType.FARM_CREATION_GRANTED, entity_id=str(account.pk)
    )


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


# --- Misma operación de onboarding ------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_concurrent_same_onboarding_request_creates_one_farm(make_user):
    account = _newcomer(make_user)
    results = _run_concurrently(
        lambda: services.create_farm(user=account, name="La Primera").farm_id,
        lambda: services.create_farm(user=account, name="La Primera").farm_id,
    )
    assert results[0] == results[1]
    assert Farm.objects.count() == 1
    assert _created_events().count() == 1
    assert _grants(account).count() == 1
    assert FarmMembership.objects.filter(user=account).count() == 1


def test_retry_after_success_returns_same_farm(make_user):
    account = _newcomer(make_user)
    first = services.create_farm(user=account, name="La Primera")
    again = services.create_farm(user=account, name="  La Primera  ")
    assert again.pk == first.pk
    assert again.farm_id == first.farm_id
    assert Farm.objects.count() == 1
    assert _created_events().count() == 1


def test_retry_after_lost_response_returns_same_farm_over_http(make_user):
    """La primera respuesta se pierde; el cliente reintenta con el mismo nombre."""
    account = _newcomer(make_user)
    client = _client(account)
    original = client.post("/api/v1/farms/", {"name": "La Primera"}, format="json")
    assert original.status_code == 201
    # (respuesta perdida en la red: el cliente no conoce `original`)
    retry = client.post("/api/v1/farms/", {"name": "La Primera"}, format="json")
    assert retry.status_code == 201
    assert retry.data["id"] == original.data["id"]
    assert retry.data["role"] == FarmRole.ADMINISTRADOR
    assert Farm.objects.count() == 1


def test_retry_outside_window_is_a_new_creation(make_user, monkeypatch):
    account = _newcomer(make_user)
    services.create_farm(user=account, name="La Primera")
    monkeypatch.setattr(services, "FIRST_FARM_REPLAY_WINDOW", timedelta(0))
    services.create_farm(user=account, name="La Primera")
    assert Farm.objects.count() == 2


# --- Creaciones adicionales legítimas -------------------------------------------


def test_additional_farm_with_other_name_is_created(make_user):
    account = _newcomer(make_user)
    services.create_farm(user=account, name="La Primera")
    services.create_farm(user=account, name="La Segunda")
    assert sorted(Farm.objects.values_list("name", flat=True)) == ["La Primera", "La Segunda"]
    assert _grants(account).count() == 1


@pytest.mark.django_db(transaction=True)
def test_concurrent_independent_requests_both_create(make_user):
    account = _newcomer(make_user)
    results = _run_concurrently(
        lambda: services.create_farm(user=account, name="Norte").farm_id,
        lambda: services.create_farm(user=account, name="Sur").farm_id,
    )
    assert len(set(results)) == 2
    assert Farm.objects.count() == 2
    assert _grants(account).count() == 1


def test_same_name_after_creating_another_farm_is_new(make_user):
    account = _newcomer(make_user)
    services.create_farm(user=account, name="La Primera")
    services.create_farm(user=account, name="La Segunda")
    services.create_farm(user=account, name="La Primera")
    assert Farm.objects.filter(name="La Primera").count() == 2


def test_capability_holder_is_not_deduplicated(make_user):
    """Sin operación de onboarding no hay reintento que reconocer (riesgo documentado)."""
    holder = make_user("22222222", can_create_farms=True)
    services.create_farm(user=holder, name="Repetida")
    services.create_farm(user=holder, name="Repetida")
    assert Farm.objects.filter(name="Repetida").count() == 2
    assert not _grants(holder).exists()


# --- Fallos, rollback y autorización -------------------------------------------


def test_failed_first_attempt_leaves_nothing_and_retry_creates(make_user, monkeypatch):
    account = _newcomer(make_user)
    real = services.record_audit_event
    calls = {"n": 0}

    def flaky(**kwargs):
        if kwargs["event_type"] == EventType.FARM_CREATION_GRANTED and calls["n"] == 0:
            calls["n"] += 1
            raise RuntimeError("auditoría caída")
        return real(**kwargs)

    monkeypatch.setattr(services, "record_audit_event", flaky)
    with pytest.raises(RuntimeError):
        services.create_farm(user=account, name="La Primera")
    assert not Farm.objects.exists()
    assert not _grants(account).exists()

    membership = services.create_farm(user=account, name="La Primera")
    assert Farm.objects.count() == 1
    assert membership.role == FarmRole.ADMINISTRADOR
    assert _grants(account).count() == 1


def test_retry_by_unverified_account_is_rejected(make_user):
    account = _newcomer(make_user)
    services.create_farm(user=account, name="La Primera")
    account.email_verified = False
    account.save(update_fields=["email_verified"])
    with pytest.raises(AppError) as exc:
        services.create_farm(user=account, name="La Primera")
    assert exc.value.status_code == 403
    assert exc.value.message == services.FARM_CREATION_EMAIL_UNVERIFIED


def test_retry_by_inactive_account_is_rejected(make_user):
    account = _newcomer(make_user)
    services.create_farm(user=account, name="La Primera")
    account.is_active = False
    account.save(update_fields=["is_active"])
    with pytest.raises(AppError) as exc:
        services.create_farm(user=account, name="La Primera")
    assert exc.value.status_code == 403


def test_retry_after_revocation_is_rejected(make_user):
    account = _newcomer(make_user)
    services.create_farm(user=account, name="La Primera")
    set_farm_creation(identification=account.identification, allowed=False, reason="baja")
    with pytest.raises(AppError) as exc:
        services.create_farm(user=account, name="La Primera")
    assert exc.value.status_code == 403
    assert Farm.objects.count() == 1


def test_invalid_name_still_400_and_forbidden_still_403(make_user):
    account = _newcomer(make_user)
    services.create_farm(user=account, name="La Primera")
    with pytest.raises(AppError) as invalid:
        services.create_farm(user=account, name="   ")
    assert invalid.value.status_code == 400

    blocked = _newcomer(make_user, "33333333")
    set_farm_creation(identification=blocked.identification, allowed=True, reason="alta")
    set_farm_creation(identification=blocked.identification, allowed=False, reason="baja")
    with pytest.raises(AppError) as forbidden:
        services.create_farm(user=blocked, name="   ")
    assert forbidden.value.status_code == 403
