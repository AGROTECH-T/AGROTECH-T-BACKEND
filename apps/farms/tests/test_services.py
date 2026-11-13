# ruff: noqa: S101
"""Pruebas unitarias de los casos de uso del dominio de fincas."""
import pytest

from apps.accounts.errors import AppError
from apps.farms import services
from apps.farms.models import Farm, FarmMembership, FarmRole

pytestmark = pytest.mark.django_db


def test_create_farm_accepts_valid_name(user):
    """El servicio crea la finca y su membresía administradora."""
    membership = services.create_farm(user=user, name="La Esperanza")

    assert membership.farm.name == "La Esperanza"
    assert membership.user == user
    assert membership.role == FarmRole.ADMINISTRADOR
    assert Farm.objects.count() == 1
    assert FarmMembership.objects.count() == 1


def test_create_farm_rejects_cuenta_sin_nombrar(make_user):
    """Una cuenta registrada no crea fincas hasta que el superadmin la nombre."""
    pendiente = make_user(identification="88888888", is_administrador=False)

    with pytest.raises(AppError) as error:
        services.create_farm(user=pendiente, name="La Esperanza")

    assert error.value.status_code == 403
    assert not Farm.objects.exists()


def test_create_farm_accepts_superadmin(make_user):
    """El superadmin abre fincas con todos los permisos de administrador."""
    superadmin = make_user(
        identification="1000000001",
        is_administrador=False,
        is_superadmin=True,
    )

    membership = services.create_farm(user=superadmin, name="La Esperanza")

    assert membership.user == superadmin
    assert membership.role == FarmRole.ADMINISTRADOR
    assert Farm.objects.count() == 1


def test_create_farm_accepts_name_at_max_length(user):
    """Un nombre de longitud máxima es válido."""
    max_length = Farm._meta.get_field("name").max_length
    name = "x" * max_length

    membership = services.create_farm(user=user, name=name)

    assert membership.farm.name == name


def test_create_farm_preserves_internal_spaces(user):
    """Los espacios interiores del nombre no se colapsan."""
    membership = services.create_farm(user=user, name="Finca   Demo")

    assert membership.farm.name == "Finca   Demo"


def test_create_farm_trims_outer_spaces(user):
    """El servicio recorta los espacios exteriores del nombre."""
    membership = services.create_farm(user=user, name="  La Esperanza\t")

    assert membership.farm.name == "La Esperanza"


@pytest.mark.parametrize("name", ["", "   ", "\t\n", " \t \n "])
def test_create_farm_rejects_blank_name(user, name):
    """Un nombre vacío o compuesto solo por espacios no se persiste."""
    with pytest.raises(AppError) as error:
        services.create_farm(user=user, name=name)

    assert error.value.status_code == 400
    assert not Farm.objects.exists()
    assert not FarmMembership.objects.exists()


def test_create_farm_rejects_name_over_max_length(user):
    """Un nombre superior al límite no se persiste."""
    max_length = Farm._meta.get_field("name").max_length

    with pytest.raises(AppError) as error:
        services.create_farm(user=user, name="x" * (max_length + 1))

    assert error.value.status_code == 400
    assert not Farm.objects.exists()
    assert not FarmMembership.objects.exists()


def test_update_farm_changes_name(user):
    """El administrador actualiza el nombre de su finca."""
    membership = services.create_farm(user=user, name="Viejo")

    updated = services.update_farm(
        user=user,
        farm_id=membership.farm_id,
        name="Nuevo",
    )

    assert updated.farm.name == "Nuevo"


def test_delete_farm_removes_memberships(user):
    """Al eliminar la finca también se borran las membresías."""
    membership = services.create_farm(user=user, name="Temporal")
    farm_id = membership.farm_id

    services.delete_farm(user=user, farm_id=farm_id)

    assert not Farm.objects.filter(pk=farm_id).exists()
    assert not FarmMembership.objects.filter(farm_id=farm_id).exists()
