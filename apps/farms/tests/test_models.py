# ruff: noqa: S101
"""Pruebas de modelos e integridad del dominio de fincas.

Propósito: verificar campos mínimos, catálogo de roles y restricciones.
Contexto: las restricciones de base de datos se comprueban sin validación previa.
"""
import pytest
from django.db import IntegrityError, transaction

from apps.farms.models import Farm, FarmMembership, FarmPermission, FarmRole


def test_role_catalog_is_exact():
    """El catálogo contiene únicamente los tres roles aprobados."""
    assert FarmRole.values == ["administrador", "operador", "consulta"]


@pytest.mark.django_db
def test_farm_is_created_with_minimal_fields():
    """Una finca se crea con nombre y marcas de tiempo automáticas."""
    farm = Farm.objects.create(name="La Esperanza")

    assert farm.pk is not None
    assert farm.name == "La Esperanza"
    assert farm.created_at is not None
    assert farm.updated_at is not None


@pytest.mark.django_db
def test_farm_names_can_repeat():
    """El nombre de una finca no es único."""
    Farm.objects.create(name="La Esperanza")
    Farm.objects.create(name="La Esperanza")

    assert Farm.objects.filter(name="La Esperanza").count() == 2


@pytest.mark.django_db
def test_user_can_belong_to_multiple_farms(user):
    """Una cuenta puede tener membresías distintas en varias fincas."""
    first = Farm.objects.create(name="Primera")
    second = Farm.objects.create(name="Segunda")

    FarmMembership.objects.create(
        farm=first,
        user=user,
        role=FarmRole.ADMINISTRADOR,
    )
    FarmMembership.objects.create(
        farm=second,
        user=user,
        role=FarmRole.CONSULTA,
    )

    assert FarmMembership.objects.filter(user=user).count() == 2
    assert set(FarmMembership.objects.filter(user=user).values_list("role", flat=True)) == {
        "administrador",
        "consulta",
    }


@pytest.mark.django_db
def test_duplicate_membership_is_rejected(user):
    """La base de datos impide dos membresías de la misma cuenta en una finca."""
    farm = Farm.objects.create(name="La Esperanza")
    FarmMembership.objects.create(
        farm=farm,
        user=user,
        role=FarmRole.ADMINISTRADOR,
    )

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            FarmMembership.objects.create(
                farm=farm,
                user=user,
                role=FarmRole.OPERADOR,
            )


@pytest.mark.django_db
def test_invalid_role_is_rejected_by_database(user):
    """La restricción CHECK rechaza roles fuera del catálogo."""
    farm = Farm.objects.create(name="La Esperanza")

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            FarmMembership.objects.create(
                farm=farm,
                user=user,
                role="propietario",
            )


@pytest.mark.django_db
def test_permiso_extra_no_se_repite_en_la_membresia(user):
    """La misma acción extra no se guarda dos veces en una membresía."""
    farm = Farm.objects.create(name="La Esperanza")
    membership = FarmMembership.objects.create(
        farm=farm,
        user=user,
        role=FarmRole.OPERADOR,
    )
    FarmPermission.objects.create(membership=membership, permission="ajustar_stock")

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            FarmPermission.objects.create(membership=membership, permission="ajustar_stock")
