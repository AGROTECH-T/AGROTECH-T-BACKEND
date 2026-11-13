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


def test_deactivate_farm_keeps_memberships(user):
    """La baja lógica conserva la finca y sus membresías."""
    membership = services.create_farm(user=user, name="Temporal")
    farm_id = membership.farm_id

    services.deactivate_farm(user=user, farm_id=farm_id)

    farm = Farm.objects.get(pk=farm_id)
    assert farm.is_active is False
    assert farm.deactivated_at is not None
    assert farm.deactivated_by == user
    assert FarmMembership.objects.filter(farm_id=farm_id).exists()


def test_delete_farm_alias_uses_deactivation(user):
    """El nombre histórico del servicio también aplica baja lógica."""
    membership = services.create_farm(user=user, name="Temporal")
    farm_id = membership.farm_id

    services.delete_farm(user=user, farm_id=farm_id)

    assert Farm.objects.get(pk=farm_id).is_active is False
