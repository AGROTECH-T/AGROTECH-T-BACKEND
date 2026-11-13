"""Modelos del dominio de fincas.

Propósito: representar fincas, la membresía de una cuenta con su rol y los
permisos que se suman a ese rol.
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
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "farms"
        ordering = ["id"]


class FarmMembership(models.Model):
    """Vincula una cuenta con una finca y define su rol en ella."""

    farm = models.ForeignKey(Farm, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="farm_memberships")
    role = models.CharField(max_length=20, choices=FarmRole.choices)
    created_at = models.DateTimeField(auto_now_add=True)

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
        ]


class FarmPermission(models.Model):
    """Permiso extra que se suma al rol de una membresía.

    Propósito: delegar una acción concreta sin cambiar el rol.
    Contexto: solo suma; nunca quita lo que el rol ya concede.
    """

    membership = models.ForeignKey(
        FarmMembership,
        on_delete=models.CASCADE,
        related_name="extra_permissions",
    )
    permission = models.CharField(max_length=40)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "farm_permissions"
        ordering = ["permission"]
        constraints = [
            models.UniqueConstraint(
                fields=("membership", "permission"),
                name="uniq_farm_permission",
            ),
        ]
