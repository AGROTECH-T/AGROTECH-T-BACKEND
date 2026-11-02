# ruff: noqa: S101
"""Pruebas HTTP del dominio de fincas.

Propósito: verificar creación atómica, roles, autenticación y aislamiento.
Contexto: usa APIClient y el login JWE real del dominio de cuentas.
"""
import pytest
from django.db import IntegrityError

from apps.farms import services
from apps.farms.models import Farm, FarmMembership, FarmRole

pytestmark = pytest.mark.django_db


def test_create_farm_assigns_admin_membership(auth_client, user):
    """El creador recibe exactamente una membresía administradora."""
    response = auth_client.post(
        "/api/v1/farms/",
        {"name": "La Esperanza"},
        format="json",
    )

    assert response.status_code == 201
    farm = Farm.objects.get(name="La Esperanza")
    memberships = FarmMembership.objects.filter(farm=farm)
    assert memberships.count() == 1
    assert memberships.get().user == user
    assert memberships.get().role == FarmRole.ADMINISTRADOR
    assert set(response.data) == {
        "id",
        "name",
        "role",
        "created_at",
        "updated_at",
    }
    assert response.data["id"] == farm.pk
    assert response.data["name"] == "La Esperanza"
    assert response.data["role"] == "administrador"
    assert response.data["created_at"] is not None
    assert response.data["updated_at"] is not None


@pytest.mark.parametrize(
    "extra",
    [
        {"role": "administrador"},
        {"user": 999},
        {"user_id": 999},
        {"campo_desconocido": "x"},
    ],
)
def test_create_farm_rejects_additional_fields(auth_client, extra):
    """El cliente solo puede enviar el campo name."""
    response = auth_client.post(
        "/api/v1/farms/",
        {"name": "La Esperanza", **extra},
        format="json",
    )

    assert response.status_code == 400
    assert response.data == {"error": "Campo no permitido."}
    assert not Farm.objects.exists()
    assert not FarmMembership.objects.exists()


def test_create_farm_rolls_back_when_membership_fails(user, monkeypatch):
    """Si falla la membresía, la finca no queda persistida."""
    def fail_membership(*args, **kwargs):
        raise IntegrityError("fallo simulado")

    monkeypatch.setattr(services, "_create_owner_membership", fail_membership)

    with pytest.raises(IntegrityError):
        services.create_farm(user=user, name="La Esperanza")

    assert not Farm.objects.filter(name="La Esperanza").exists()


@pytest.mark.parametrize("name", ["", "   ", "\t\n"])
def test_create_farm_rejects_blank_name(auth_client, name):
    """Un nombre vacío o compuesto solo por espacios no se acepta."""
    response = auth_client.post(
        "/api/v1/farms/",
        {"name": name},
        format="json",
    )

    assert response.status_code == 400
    assert not Farm.objects.exists()
    assert not FarmMembership.objects.exists()


def test_create_farm_rejects_name_over_100_characters(auth_client):
    """Un nombre de 101 caracteres supera el límite permitido."""
    response = auth_client.post(
        "/api/v1/farms/",
        {"name": "x" * 101},
        format="json",
    )

    assert response.status_code == 400
    assert not Farm.objects.exists()
    assert not FarmMembership.objects.exists()


def test_create_farm_accepts_100_character_name(auth_client):
    """Un nombre de exactamente 100 caracteres es válido."""
    name = "x" * 100

    response = auth_client.post(
        "/api/v1/farms/",
        {"name": name},
        format="json",
    )

    assert response.status_code == 201
    assert response.data["name"] == name
    assert Farm.objects.get().name == name


def test_create_farm_preserves_internal_spaces(auth_client):
    """Los espacios internos del nombre no se colapsan."""
    name = "Finca   Demo"

    response = auth_client.post(
        "/api/v1/farms/",
        {"name": name},
        format="json",
    )

    assert response.status_code == 201
    assert response.data["name"] == name
    assert Farm.objects.get().name == name


def test_list_is_empty_without_memberships(auth_client):
    """Una cuenta sin membresías recibe una lista vacía."""
    response = auth_client.get("/api/v1/farms/")

    assert response.status_code == 200
    assert response.data == []


def test_list_returns_only_user_farms(login, make_user):
    """El listado no expone fincas de otras cuentas."""
    owner = make_user("11111111")
    other = make_user("22222222")
    own_farm = Farm.objects.create(name="Propia")
    foreign_farm = Farm.objects.create(name="Ajena")
    FarmMembership.objects.create(
        farm=own_farm,
        user=owner,
        role=FarmRole.ADMINISTRADOR,
    )
    FarmMembership.objects.create(
        farm=foreign_farm,
        user=other,
        role=FarmRole.ADMINISTRADOR,
    )

    response = login(owner).get("/api/v1/farms/")

    assert response.status_code == 200
    assert [row["id"] for row in response.data] == [own_farm.pk]


def test_list_includes_role_for_each_farm(login, make_user):
    """Cada elemento del listado informa el rol de la cuenta actual."""
    account = make_user("11111111")
    administered = Farm.objects.create(name="Administrada")
    consulted = Farm.objects.create(name="Consultada")
    FarmMembership.objects.create(
        farm=administered,
        user=account,
        role=FarmRole.ADMINISTRADOR,
    )
    FarmMembership.objects.create(
        farm=consulted,
        user=account,
        role=FarmRole.CONSULTA,
    )

    response = login(account).get("/api/v1/farms/")

    assert response.status_code == 200
    assert {row["id"]: row["role"] for row in response.data} == {
        administered.pk: "administrador",
        consulted.pk: "consulta",
    }


def test_member_can_retrieve_own_farm(login, user):
    """Una cuenta miembro puede consultar el detalle de su finca."""
    farm = Farm.objects.create(name="La Esperanza")
    FarmMembership.objects.create(
        farm=farm,
        user=user,
        role=FarmRole.OPERADOR,
    )

    response = login(user).get(f"/api/v1/farms/{farm.pk}/")

    assert response.status_code == 200
    assert response.data["id"] == farm.pk
    assert response.data["name"] == "La Esperanza"
    assert response.data["role"] == "operador"


def test_non_member_gets_not_found(login, make_user):
    """Una finca existente ajena responde 404, igual que una inexistente."""
    owner = make_user("11111111")
    outsider = make_user("22222222")
    farm = Farm.objects.create(name="La Esperanza")
    FarmMembership.objects.create(
        farm=farm,
        user=owner,
        role=FarmRole.ADMINISTRADOR,
    )

    response = login(outsider).get(f"/api/v1/farms/{farm.pk}/")

    assert response.status_code == 404
    assert set(response.data) == {"error"}


def test_missing_farm_gets_not_found(auth_client):
    """Un identificador inexistente responde 404."""
    response = auth_client.get("/api/v1/farms/999999/")

    assert response.status_code == 404
    assert set(response.data) == {"error"}


def test_foreign_and_missing_farm_return_same_404_body(login, make_user):
    """Una finca ajena y una inexistente no se distinguen en la respuesta."""
    owner = make_user("11111111")
    outsider = make_user("22222222")
    farm = Farm.objects.create(name="La Esperanza")
    FarmMembership.objects.create(
        farm=farm,
        user=owner,
        role=FarmRole.ADMINISTRADOR,
    )

    client = login(outsider)
    foreign = client.get(f"/api/v1/farms/{farm.pk}/")
    missing = client.get("/api/v1/farms/999999/")

    assert foreign.status_code == 404
    assert missing.status_code == 404
    assert foreign.data == missing.data


def test_anonymous_requests_are_rejected(client, user):
    """Las tres rutas exigen la autenticación Bearer existente."""
    farm = Farm.objects.create(name="La Esperanza")
    FarmMembership.objects.create(
        farm=farm,
        user=user,
        role=FarmRole.ADMINISTRADOR,
    )

    assert client.get("/api/v1/farms/").status_code == 401
    assert (
        client.post("/api/v1/farms/", {"name": "Nueva"}, format="json").status_code
        == 401
    )
    assert client.get(f"/api/v1/farms/{farm.pk}/").status_code == 401
