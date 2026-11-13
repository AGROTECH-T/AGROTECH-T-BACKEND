# ruff: noqa: S101, S106
"""Pruebas de la capacidad global de crear fincas y de las capacidades por finca (BUILD F6).

Propósito: verificar que crear fincas depende solo de ``can_create_farms``, que
las capacidades expuestas coinciden con lo que los servicios permiten y que
el aislamiento entre fincas y las reglas de F4/F5 se conservan.
Contexto: usa las fixtures de farms; ``make_user`` concede la capacidad por
defecto y estas pruebas la niegan de forma explícita cuando corresponde.
"""
import hashlib
from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.management import CommandError, call_command
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.errors import AppError
from apps.accounts.services.capability_service import set_farm_creation
from apps.farms import invitation_services, services
from apps.farms.capabilities import (
    CAPABILITY_KEYS,
    INVENTORY_WRITE_ROLES,
    can_create_farms,
    farm_capabilities,
)
from apps.farms.models import (
    Farm,
    FarmInvitation,
    FarmMembership,
    FarmRole,
    InvitationStatus,
)
from apps.farms.tests.conftest import VALID_PASSWORD
from apps.inventario import services as inventory
from apps.inventario.catalog import InventoryCategory, InventoryUnit
from apps.inventario.models import MovementKind
from apps.inventario.services.membership import WRITE_ROLES
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


def _farm(admin, name="Finca") -> int:
    return services.create_farm(user=admin, name=name).farm_id


def _join(farm_id, account, role):
    return FarmMembership.objects.create(farm_id=farm_id, user=account, role=role)


def _product(admin, farm_id):
    return inventory.create_product(
        user=admin,
        farm_id=farm_id,
        name="Maíz",
        barcode="7701234567890",
        unit=InventoryUnit.KG,
        category=InventoryCategory.CONCENTRADOS,
        min_stock=Decimal("1"),
    )


# --- Creación de fincas: capacidad global ---


def test_account_with_capability_creates_farm(make_user):
    owner = make_user("11111111", can_create_farms=True)
    response = _client(owner).post("/api/v1/farms/", {"name": "La Esperanza"}, format="json")
    assert response.status_code == 201
    assert response.data["role"] == FarmRole.ADMINISTRADOR
    assert response.data["capabilities"]["can_manage_members"] is True


def _exhaust_first_farm(account):
    """Agota la excepción de primera finca (F10.2) con un retiro explícito."""
    set_farm_creation(identification=account.identification, allowed=True, reason="alta")
    set_farm_creation(identification=account.identification, allowed=False, reason="baja")


def test_account_without_capability_cannot_create_farm(make_user):
    # F10.2: sin capacidad solo se crea la primera finca; aquí ya no aplica.
    account = make_user("11111111", can_create_farms=False)
    _exhaust_first_farm(account)
    response = _client(account).post("/api/v1/farms/", {"name": "Prohibida"}, format="json")
    assert response.status_code == 403
    assert response.data == {"error": services.FARM_CREATION_FORBIDDEN}
    assert not Farm.objects.filter(name="Prohibida").exists()
    assert not AuditEvent.objects.filter(event_type=EventType.FARM_CREATED).exists()


def test_admin_of_another_farm_without_capability_cannot_create(make_user):
    founder = make_user("11111111")
    admin = make_user("22222222", can_create_farms=False)
    farm_id = _farm(founder)
    _join(farm_id, admin, FarmRole.ADMINISTRADOR)
    with pytest.raises(AppError) as exc:
        services.create_farm(user=admin, name="Otra")
    assert exc.value.status_code == 403


@pytest.mark.parametrize("role", [FarmRole.OPERADOR, FarmRole.CONSULTA])
def test_operator_and_viewer_cannot_create_farm(make_user, role):
    founder = make_user("11111111")
    member = make_user("22222222", can_create_farms=False)
    _join(_farm(founder), member, role)
    # F10.2: el rol no concede crear; sin la excepción de primera finca, 403.
    _exhaust_first_farm(member)
    response = _client(member).post("/api/v1/farms/", {"name": "Nueva"}, format="json")
    assert response.status_code == 403


def test_inactive_account_never_creates_even_with_flag(make_user):
    account = make_user("11111111", can_create_farms=True, is_active=False)
    assert can_create_farms(account) is False
    with pytest.raises(AppError):
        services.create_farm(user=account, name="X")


def test_capability_not_granted_by_invitation_or_lost_on_leave(make_user):
    admin = make_user("11111111")
    farm_id = _farm(admin)
    invited = make_user(
        "22222222", correo="inv@example.com", email_verified=True, can_create_farms=False
    )
    FarmInvitation.objects.create(
        farm_id=farm_id,
        email="inv@example.com",
        pending_email="inv@example.com",
        role=FarmRole.ADMINISTRADOR,
        status=InvitationStatus.PENDING,
        token_hash=hashlib.sha256(b"tok").hexdigest(),
        expires_at=timezone.now() + timedelta(days=7),
        invited_by=admin,
    )
    invitation_services.accept_invitation(user=invited, token="tok")
    invited.refresh_from_db()
    assert invited.can_create_farms is False

    member = make_user("33333333", can_create_farms=True)
    _join(farm_id, member, FarmRole.OPERADOR)
    services.leave_farm(user=member, farm_id=farm_id)
    member.refresh_from_db()
    assert member.can_create_farms is True


# --- Capacidades por finca ---

EXPECTED = {
    (FarmRole.ADMINISTRADOR, True): {
        "can_edit_farm": True, "can_deactivate_farm": True, "can_reactivate_farm": False,
        "can_view_history": True, "can_manage_members": True, "can_manage_invitations": True,
        "can_read_inventory": True, "can_write_inventory": True,
    },
    (FarmRole.ADMINISTRADOR, False): {
        "can_edit_farm": False, "can_deactivate_farm": False, "can_reactivate_farm": True,
        "can_view_history": True, "can_manage_members": False, "can_manage_invitations": False,
        "can_read_inventory": False, "can_write_inventory": False,
    },
    (FarmRole.OPERADOR, True): {
        "can_edit_farm": False, "can_deactivate_farm": False, "can_reactivate_farm": False,
        "can_view_history": False, "can_manage_members": False, "can_manage_invitations": False,
        "can_read_inventory": True, "can_write_inventory": True,
    },
    (FarmRole.CONSULTA, True): {
        "can_edit_farm": False, "can_deactivate_farm": False, "can_reactivate_farm": False,
        "can_view_history": False, "can_manage_members": False, "can_manage_invitations": False,
        "can_read_inventory": True, "can_write_inventory": False,
    },
}


@pytest.mark.parametrize(("role", "farm_active"), list(EXPECTED))
def test_capability_matrix(make_user, role, farm_active):
    founder = make_user("11111111")
    farm_id = _farm(founder)
    member = founder
    if role != FarmRole.ADMINISTRADOR:
        member = make_user("22222222")
        _join(farm_id, member, role)
    if not farm_active:
        services.deactivate_farm(user=founder, farm_id=farm_id)
    membership = FarmMembership.objects.select_related("farm", "user").get(
        farm_id=farm_id, user=member
    )
    caps = farm_capabilities(membership)
    assert tuple(caps) == CAPABILITY_KEYS
    assert caps == EXPECTED[(role, farm_active)]


def test_inventory_write_roles_have_single_source():
    assert WRITE_ROLES is INVENTORY_WRITE_ROLES


def test_capabilities_change_with_selected_farm(make_user):
    account = make_user("11111111")
    other = make_user("22222222")
    own_id = _farm(account, "Propia")
    guest_id = _farm(other, "Ajena")
    _join(guest_id, account, FarmRole.CONSULTA)

    farms = {item["id"]: item for item in _client(account).get("/api/v1/farms/").data}
    assert farms[own_id]["capabilities"]["can_manage_members"] is True
    assert farms[guest_id]["capabilities"]["can_manage_members"] is False
    assert farms[guest_id]["capabilities"]["can_write_inventory"] is False


# --- Paridad: la capacidad coincide con lo que el backend permite ---


def test_operator_cannot_manage_members_or_invitations(make_user):
    admin = make_user("11111111")
    operator = make_user("22222222")
    farm_id = _farm(admin)
    _join(farm_id, operator, FarmRole.OPERADOR)
    client = _client(operator)
    detail = client.get(f"/api/v1/farms/{farm_id}/")
    assert detail.data["capabilities"]["can_manage_invitations"] is False
    invite = client.post(
        f"/api/v1/farms/{farm_id}/invitations/",
        {"email": "x@example.com", "role": FarmRole.OPERADOR},
        format="json",
    )
    assert invite.status_code == 403
    assert client.get(f"/api/v1/farms/{farm_id}/members/").status_code == 403
    edit = client.put(f"/api/v1/farms/{farm_id}/", {"name": "Hackeada"}, format="json")
    assert edit.status_code == 403
    assert Farm.objects.get(pk=farm_id).name == "Finca"


def test_viewer_cannot_write_inventory_but_operator_can(make_user):
    admin = make_user("11111111")
    viewer = make_user("22222222")
    operator = make_user("33333333")
    farm_id = _farm(admin)
    _join(farm_id, viewer, FarmRole.CONSULTA)
    _join(farm_id, operator, FarmRole.OPERADOR)
    product = _product(admin, farm_id)

    viewer_caps = _client(viewer).get(f"/api/v1/farms/{farm_id}/").data["capabilities"]
    assert viewer_caps["can_write_inventory"] is False
    with pytest.raises(AppError) as exc:
        inventory.register_movement(
            user=viewer, farm_id=farm_id, product_id=product.pk,
            kind=MovementKind.ENTRADA, quantity=Decimal("1"), note="",
        )
    assert exc.value.status_code == 403

    operator_caps = _client(operator).get(f"/api/v1/farms/{farm_id}/").data["capabilities"]
    assert operator_caps["can_write_inventory"] is True
    inventory.register_movement(
        user=operator, farm_id=farm_id, product_id=product.pk,
        kind=MovementKind.ENTRADA, quantity=Decimal("1"), note="",
    )


def test_foreign_farm_is_not_visible(make_user):
    owner = make_user("11111111")
    stranger = make_user("22222222")
    farm_id = _farm(owner)
    client = _client(stranger)
    assert client.get(f"/api/v1/farms/{farm_id}/").status_code == 404
    assert client.get(f"/api/v1/farms/{farm_id}/members/").status_code == 404
    assert client.get(f"/api/v1/farms/{farm_id}/invitations/").status_code == 404
    assert client.get(f"/api/v1/inventory/{farm_id}/products/").status_code == 404


# --- /auth/me ---


def test_me_exposes_account_capabilities_read_only(make_user):
    founder = make_user("11111111", can_create_farms=True)
    viewer = make_user("22222222", can_create_farms=False)
    _join(_farm(founder), viewer, FarmRole.CONSULTA)

    founder_me = _client(founder).get("/api/v1/auth/me/").data
    assert founder_me["can_create_farms"] is True
    assert founder_me["can_open_inactive_farms"] is True

    client = _client(viewer)
    me = client.get("/api/v1/auth/me/").data
    assert me["can_create_farms"] is False
    assert me["can_open_inactive_farms"] is False
    client.patch("/api/v1/auth/me/", {"can_create_farms": True}, format="json")
    viewer.refresh_from_db()
    assert viewer.can_create_farms is False


# --- Concesión explícita y auditada ---


OS_USER = "operador-local"


def _events(event_type):
    return AuditEvent.objects.filter(event_type=event_type)


@pytest.fixture
def os_user(monkeypatch):
    """Fija el usuario del sistema operativo que verá el comando."""
    monkeypatch.setattr(
        "apps.accounts.services.capability_service.getpass.getuser", lambda: OS_USER
    )
    return OS_USER


def test_grant_records_reason_and_os_user(make_user, os_user):
    account = make_user("11111111", can_create_farms=False)
    call_command("grant_farm_creation", "--identification", "11111111", "--reason", "piloto")
    account.refresh_from_db()
    assert account.can_create_farms is True
    event = _events(EventType.FARM_CREATION_GRANTED).get()
    assert event.actor is None
    assert event.entity_type == "User"
    assert event.entity_id == str(account.pk)
    assert event.reason == "piloto"
    assert event.before_data == {"can_create_farms": False}
    assert event.after_data == {"can_create_farms": True}
    assert event.metadata == {"executed_by": os_user, "execution_source": "management_command"}


def test_revoke_records_reason_and_os_user(make_user, os_user):
    account = make_user("11111111", can_create_farms=True)
    call_command("revoke_farm_creation", "--identification", "11111111", "--reason", "baja")
    account.refresh_from_db()
    assert account.can_create_farms is False
    event = _events(EventType.FARM_CREATION_REVOKED).get()
    assert event.reason == "baja"
    assert event.metadata["executed_by"] == os_user


def test_repeating_grant_or_revoke_does_not_duplicate_events(make_user, os_user):
    make_user("11111111", can_create_farms=False)
    for _ in range(2):
        call_command("grant_farm_creation", "--identification", "11111111", "--reason", "x")
    assert _events(EventType.FARM_CREATION_GRANTED).count() == 1
    for _ in range(2):
        call_command("revoke_farm_creation", "--identification", "11111111", "--reason", "y")
    assert _events(EventType.FARM_CREATION_REVOKED).count() == 1
    assert set_farm_creation(identification="11111111", allowed=False, reason="z") is False


@pytest.mark.parametrize("command", ["grant_farm_creation", "revoke_farm_creation"])
def test_reason_is_required(make_user, command):
    account = make_user("11111111", can_create_farms=False)
    with pytest.raises(CommandError):
        call_command(command, "--identification", "11111111")
    for blank in ("", "   "):
        with pytest.raises(CommandError):
            call_command(command, "--identification", "11111111", "--reason", blank)
    with pytest.raises(AppError) as exc:
        set_farm_creation(identification="11111111", allowed=True, reason="  ")
    assert exc.value.status_code == 400
    account.refresh_from_db()
    assert account.can_create_farms is False
    assert not AuditEvent.objects.filter(entity_type="User", entity_id=str(account.pk)).exists()


def test_grant_command_rejects_unknown_account():
    with pytest.raises(CommandError):
        call_command("grant_farm_creation", "--identification", "99999999", "--reason", "x")


def test_audit_failure_leaves_no_partial_change(make_user, monkeypatch):
    account = make_user("11111111", can_create_farms=False)

    def fail(**kwargs):
        raise RuntimeError("auditoría caída")

    monkeypatch.setattr("apps.accounts.services.capability_service.record_audit_event", fail)
    with pytest.raises(RuntimeError):
        set_farm_creation(identification="11111111", allowed=True, reason="alta")
    account.refresh_from_db()
    assert account.can_create_farms is False


def test_os_user_unavailable_falls_back_without_failing(make_user, monkeypatch):
    make_user("11111111", can_create_farms=False)

    def boom():
        raise OSError("sin usuario")

    monkeypatch.setattr("apps.accounts.services.capability_service.getpass.getuser", boom)
    call_command("grant_farm_creation", "--identification", "11111111", "--reason", "x")
    event = _events(EventType.FARM_CREATION_GRANTED).get()
    assert event.metadata["executed_by"] == "desconocido"


# --- Esquema OpenAPI ---


def test_openapi_documents_403_and_required_capabilities():
    from drf_spectacular.generators import SchemaGenerator

    schema = SchemaGenerator().get_schema(request=None, public=True)
    post = schema["paths"]["/api/v1/farms/"]["post"]["responses"]
    assert "403" in post
    components = schema["components"]["schemas"]
    caps = components["FarmCapabilities"]
    assert sorted(caps["required"]) == sorted(CAPABILITY_KEYS)
    assert all(prop.get("readOnly") for prop in caps["properties"].values())
    assert "can_leave_farm" not in caps["properties"]
    assert "capabilities" in components["FarmResponse"]["required"]
    account = components["AccountResponse"]
    for key in ("can_create_farms", "can_open_inactive_farms"):
        assert key in account["required"]
        assert account["properties"][key]["readOnly"] is True
