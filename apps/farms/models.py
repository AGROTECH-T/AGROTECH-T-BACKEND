"""Modelos del dominio de fincas.

Propósito: representar fincas y la membresía de una cuenta con su rol.
Contexto: una cuenta puede pertenecer a varias fincas con un rol distinto.
"""
from django.db import models

from apps.accounts.models import User


class FarmRole(models.TextChoices):
    """Catálogo de roles de una membresía dentro de una finca."""

    ADMINISTRADOR = "administrador", "Administrador"
    OPERADOR = "operador", "Operador"
    CONSULTA = "consulta", "Consulta"


class Farm(models.Model):
    """Representa una finca sin atributos geográficos ni comerciales."""

    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)
    deactivated_at = models.DateTimeField(blank=True, null=True)
    deactivated_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="deactivated_farms",
        blank=True,
        null=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "farms"
        ordering = ["id"]


class MembershipStatus(models.TextChoices):
    """Estados del ciclo de vida de una membresía."""

    ACTIVE = "active", "Activa"
    REVOKED = "revoked", "Revocada"
    LEFT = "left", "Abandonada"


class FarmMembership(models.Model):
    """Vincula una cuenta con una finca y define su rol y estado en ella."""

    farm = models.ForeignKey(Farm, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="farm_memberships")
    role = models.CharField(max_length=20, choices=FarmRole.choices)
    status = models.CharField(
        max_length=20,
        choices=MembershipStatus.choices,
        default=MembershipStatus.ACTIVE,
    )
    revoked_at = models.DateTimeField(blank=True, null=True)
    revoked_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="revoked_memberships",
        blank=True,
        null=True,
    )
    left_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "farm_memberships"
        ordering = ["farm_id"]
        constraints = [
            models.UniqueConstraint(
                fields=("farm", "user"),
                name="uniq_farm_membership_user",
            ),
            models.CheckConstraint(
                condition=models.Q(role__in=FarmRole.values),
                name="farms_membership_role_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=MembershipStatus.values),
                name="farms_membership_status_valid",
            ),
        ]
        indexes = [
            models.Index(
                fields=["farm", "status", "role"],
                name="farms_membership_status_idx",
            ),
        ]
