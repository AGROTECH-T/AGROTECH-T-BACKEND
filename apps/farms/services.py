"""Casos de uso del dominio de fincas.

Propósito: crear fincas con su membresía inicial y consultar membresías.
Contexto: el usuario autenticado se resuelve fuera del servicio.
"""
from django.db import transaction

from apps.accounts.errors import AppError
from apps.accounts.models import User
from apps.farms.models import Farm, FarmMembership, FarmRole


def create_farm(*, user: User, name: str) -> FarmMembership:
    """Crea una finca y la membresía administradora en una sola transacción."""
    validated_name = _validate_farm_name(name)
    with transaction.atomic():
        farm = Farm.objects.create(name=validated_name)
        return _create_owner_membership(farm, user)


def _validate_farm_name(name: str) -> str:
    """Normaliza y valida el nombre antes de persistir la finca."""
    normalized = name.strip()
    if not normalized:
        raise AppError("el nombre de la finca es obligatorio", 400)

    max_length = Farm._meta.get_field("name").max_length
    if len(normalized) > max_length:
        raise AppError(
            f"el nombre de la finca no puede superar los {max_length} caracteres",
            400,
        )
    return normalized


def list_memberships(user: User):
    """Retorna las membresías del usuario con su finca precargada."""
    return (
        FarmMembership.objects.filter(user=user)
        .select_related("farm")
        .order_by("farm_id")
    )


def get_membership(*, user: User, farm_id: int) -> FarmMembership | None:
    """Retorna la membresía del usuario en la finca o None si no pertenece."""
    return (
        FarmMembership.objects.filter(user=user, farm_id=farm_id)
        .select_related("farm")
        .first()
    )


def _create_owner_membership(farm: Farm, user: User) -> FarmMembership:
    """Asigna el rol administrador al creador de la finca."""
    return FarmMembership.objects.create(
        farm=farm,
        user=user,
        role=FarmRole.ADMINISTRADOR,
    )
