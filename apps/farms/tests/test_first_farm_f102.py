# ruff: noqa: S101
"""Pruebas de F10.2: creación de la primera finca desde el onboarding.

Propósito: una cuenta activa y verificada sin la capacidad global puede crear
su primera finca; queda como administradora y recibe ``can_create_farms`` en
la misma transacción. La excepción no se reutiliza ni elude un retiro.
Contexto: la regla está en ``services.create_farm`` y en
``capabilities.first_farm_available``; /auth/me solo la informa.
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.core.exceptions import FieldDoesNotExist
from django.db import connections
from rest_framework.test import APIClient

from apps.accounts.errors import AppError
from apps.accounts.models import User
from apps.accounts.services.capability_service import set_farm_creation
from apps.farms import services
from apps.farms.capabilities import can_create_first_farm, first_farm_available
from apps.farms.models import Farm, FarmMembership, FarmRole, MembershipStatus
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
    """Cuenta nueva, activa y verificada, sin la capacidad global."""
    return make_user(identification, can_create_farms=False, **kwargs)


def _grants(account):
    return AuditEvent.objects.filter(
        event_type=EventType.FARM_CREATION_GRANTED,
        entity_type="User",
        entity_id=str(account.pk),
    )


def _nothing_created(account):
    account.refresh_from_db()
    assert account.can_create_farms is False
    assert not Farm.objects.exists()
    assert not FarmMembership.objects.filter(user=account).exists()
    assert not AuditEvent.objects.filter(event_type=EventType.FARM_CREATED).exists()
    assert not _grants(account).exists()


# --- Camino feliz ------------------------------------------------------------


def test_verified_newcomer_creates_first_farm_as_admin(make_user):
    account = _newcomer(make_user)
    response = _client(account).post("/api/v1/farms/", {"name": "La Primera"}, format="json")

    assert response.status_code == 201
    assert response.data["name"] == "La Primera"
    assert response.data["role"] == FarmRole.ADMINISTRADOR
    assert response.data["capabilities"]["can_manage_members"] is True
    assert response.data["capabilities"]["can_manage_invitations"] is True
    membership = FarmMembership.objects.get(user=account)
    assert membership.role == FarmRole.ADMINISTRADOR
    assert membership.status == MembershipStatus.ACTIVE
    account.refresh_from_db()
    assert account.can_create_farms is True


def test_first_farm_grant_is_audited_with_its_source(make_user):
    account = _newcomer(make_user)
    farm_id = services.create_farm(user=account, name="Auditada").farm_id

    created = AuditEvent.objects.get(event_type=EventType.FARM_CREATED)
    assert created.actor_id == account.pk
    grant = _grants(account).get()
    assert grant.actor_id == account.pk
    assert grant.farm_id is None
    assert grant.before_data == {"can_create_farms": False}
    assert grant.after_data == {"can_create_farms": True}
    assert grant.reason == services.FIRST_FARM_REASON
    assert grant.metadata == {
        "execution_source": services.FIRST_FARM_SOURCE,
        "farm_id": farm_id,
    }


def test_me_reports_first_farm_without_granting(make_user):
    account = _newcomer(make_user)
    client = _client(account)
    for _ in range(2):
        me = client.get("/api/v1/auth/me/").data
        assert me["can_create_farms"] is False
        assert me["can_create_first_farm"] is True
    account.refresh_from_db()
    assert account.can_create_farms is False
    assert not _grants(account).exists()

    client.post("/api/v1/farms/", {"name": "Mía"}, format="json")
    me = client.get("/api/v1/auth/me/").data
    assert me["can_create_farms"] is True
    assert me["can_create_first_farm"] is False
    assert me["can_open_inactive_farms"] is True


def test_more_farms_use_the_granted_capability_without_new_grant(make_user):
    account = _newcomer(make_user)
    services.create_farm(user=account, name="Primera")
    services.create_farm(user=account, name="Segunda")
    assert FarmMembership.objects.filter(user=account, role=FarmRole.ADMINISTRADOR).count() == 2
    assert _grants(account).count() == 1


def test_member_of_other_farm_can_create_own_first_farm(make_user):
    founder = make_user("99999999")
    other = services.create_farm(user=founder, name="Ajena")
    account = _newcomer(make_user)
    FarmMembership.objects.create(farm_id=other.farm_id, user=account, role=FarmRole.OPERADOR)

    services.create_farm(user=account, name="Propia")

    assert FarmMembership.objects.get(farm_id=other.farm_id, user=account).role == FarmRole.OPERADOR
    account.refresh_from_db()
    assert account.can_create_farms is True


# --- Rechazos -----------------------------------------------------------------


def test_unverified_account_is_rejected(make_user):
    account = _newcomer(make_user, email_verified=False)
    response = _client(account).post("/api/v1/farms/", {"name": "X"}, format="json")
    assert response.status_code == 403
    assert response.data == {"error": services.FARM_CREATION_EMAIL_UNVERIFIED}
    _nothing_created(account)
    assert can_create_first_farm(account) is False


def test_unverified_account_with_flag_is_rejected(make_user):
    account = make_user("11111111", can_create_farms=True, email_verified=False)
    response = _client(account).post("/api/v1/farms/", {"name": "X"}, format="json")
    assert response.status_code == 403
    assert response.data == {"error": services.FARM_CREATION_EMAIL_UNVERIFIED}
    assert _client(account).get("/api/v1/auth/me/").data["can_create_farms"] is False
    account.refresh_from_db()
    assert account.can_create_farms is True  # la bandera no se retira


def test_inactive_account_is_rejected(make_user):
    account = _newcomer(make_user, is_active=False)
    assert can_create_first_farm(account) is False
    with pytest.raises(AppError) as exc:
        services.create_farm(user=account, name="X")
    assert exc.value.status_code == 403
    _nothing_created(account)


def test_exception_cannot_be_reused_after_revocation(make_user):
    account = _newcomer(make_user)
    services.create_farm(user=account, name="Primera")
    set_farm_creation(identification=account.identification, allowed=False, reason="baja")

    with pytest.raises(AppError) as exc:
        services.create_farm(user=account, name="Segunda")
    assert exc.value.status_code == 403
    assert exc.value.message == services.FARM_CREATION_FORBIDDEN
    assert Farm.objects.count() == 1
    account.refresh_from_db()
    assert account.can_create_farms is False
    assert first_farm_available(account) is False


def test_exception_unavailable_after_leaving_own_farm(make_user):
    account = _newcomer(make_user)
    farm_id = services.create_farm(user=account, name="Primera").farm_id
    set_farm_creation(identification=account.identification, allowed=False, reason="baja")
    second_admin = make_user("22222222")
    FarmMembership.objects.create(farm_id=farm_id, user=second_admin, role=FarmRole.ADMINISTRADOR)
    services.leave_farm(user=account, farm_id=farm_id)

    with pytest.raises(AppError):
        services.create_farm(user=account, name="Otra")


@pytest.mark.parametrize(
    "status", [MembershipStatus.ACTIVE, MembershipStatus.LEFT, MembershipStatus.REVOKED]
)
def test_any_past_admin_membership_blocks_exception(make_user, status):
    """Cubre fincas creadas antes de que existiera la auditoría de creación."""
    founder = make_user("99999999")
    farm_id = services.create_farm(user=founder, name="Legada").farm_id
    account = _newcomer(make_user)
    FarmMembership.objects.create(
        farm_id=farm_id, user=account, role=FarmRole.ADMINISTRADOR, status=status
    )
    assert first_farm_available(account) is False
    with pytest.raises(AppError) as exc:
        services.create_farm(user=account, name="Nueva")
    assert exc.value.status_code == 403


def test_past_farm_creation_event_blocks_exception(make_user):
    account = _newcomer(make_user)
    AuditEvent.objects.create(
        actor=account, event_type=EventType.FARM_CREATED, entity_type="farm", entity_id="1"
    )
    assert first_farm_available(account) is False


def test_command_grant_history_blocks_exception(make_user):
    account = _newcomer(make_user)
    set_farm_creation(identification=account.identification, allowed=True, reason="alta")
    set_farm_creation(identification=account.identification, allowed=False, reason="baja")
    assert can_create_first_farm(account) is False
    with pytest.raises(AppError):
        services.create_farm(user=account, name="X")


def test_invalid_name_grants_nothing(make_user):
    account = _newcomer(make_user)
    response = _client(account).post("/api/v1/farms/", {"name": "   "}, format="json")
    assert response.status_code == 400
    _nothing_created(account)
    assert can_create_first_farm(account) is True


# --- Atomicidad ---------------------------------------------------------------


@pytest.mark.parametrize("failing", [EventType.FARM_CREATED, EventType.FARM_CREATION_GRANTED])
def test_audit_failure_rolls_back_everything(make_user, monkeypatch, failing):
    account = _newcomer(make_user)
    real = services.record_audit_event

    def record(**kwargs):
        if kwargs["event_type"] == failing:
            raise RuntimeError("auditoría caída")
        return real(**kwargs)

    monkeypatch.setattr(services, "record_audit_event", record)
    with pytest.raises(RuntimeError):
        services.create_farm(user=account, name="Fallida")
    _nothing_created(account)


def test_capability_save_failure_rolls_back_farm(make_user, monkeypatch):
    account = _newcomer(make_user)
    original = User.save

    def save(self, *args, **kwargs):
        if "can_create_farms" in (kwargs.get("update_fields") or ()):
            raise RuntimeError("fallo al guardar")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(User, "save", save)
    with pytest.raises(RuntimeError):
        services.create_farm(user=account, name="Fallida")
    _nothing_created(account)


@pytest.mark.django_db(transaction=True)
def test_concurrent_first_farm_requests_grant_once(make_user):
    account = _newcomer(make_user)
    barrier = Barrier(2)

    def create(name):
        barrier.wait(timeout=5)
        try:
            return services.create_farm(user=account, name=name).farm_id
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(create, ["A", "B"]))

    assert all(results)
    assert _grants(account).count() == 1
    account.refresh_from_db()
    assert account.can_create_farms is True


# --- Privilegios técnicos y cuentas existentes ---------------------------------


def test_no_technical_privileges_exist_or_are_granted(make_user):
    for field in ("is_staff", "is_superuser"):
        with pytest.raises(FieldDoesNotExist):
            User._meta.get_field(field)
    account = _newcomer(make_user)
    services.create_farm(user=account, name="Primera")
    account.refresh_from_db()
    assert getattr(account, "is_superuser", False) is False
    assert getattr(account, "is_staff", False) is False


def test_existing_capability_holders_keep_it_without_new_events(make_user):
    holder = make_user("11111111", can_create_farms=True)
    services.create_farm(user=holder, name="Normal")
    holder.refresh_from_db()
    assert holder.can_create_farms is True
    assert not _grants(holder).exists()


def test_rejected_attempt_does_not_touch_other_accounts(make_user):
    holder = make_user("33333333", can_create_farms=True)
    blocked = _newcomer(make_user, email_verified=False)
    with pytest.raises(AppError):
        services.create_farm(user=blocked, name="X")
    holder.refresh_from_db()
    blocked.refresh_from_db()
    assert holder.can_create_farms is True
    assert blocked.can_create_farms is False
