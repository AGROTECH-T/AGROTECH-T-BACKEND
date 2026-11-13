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


class InvitationStatus(models.TextChoices):
    """Estados del ciclo de vida de una invitación."""

    PENDING = "pending", "Pendiente"
    ACCEPTED = "accepted", "Aceptada"
    CANCELLED = "cancelled", "Cancelada"
    EXPIRED = "expired", "Expirada"


class FarmInvitation(models.Model):
    """Invitación de un solo uso para incorporar un correo a una finca."""

    farm = models.ForeignKey(Farm, on_delete=models.CASCADE, related_name="invitations")
    email = models.EmailField(max_length=254)
    pending_email = models.EmailField(max_length=254, blank=True, null=True)
    role = models.CharField(max_length=20, choices=FarmRole.choices)
    status = models.CharField(
        max_length=20,
        choices=InvitationStatus.choices,
        default=InvitationStatus.PENDING,
    )
    token_hash = models.CharField(max_length=64, blank=True, default="")
    # Huella HMAC-SHA256 del código (apps.farms.invitation_codes). NULL en las
    # invitaciones por enlace y cuando se invalida; nunca "" (chocaría en el
    # índice único, que en MariaDB admite varios NULL).
    code_hash = models.CharField(max_length=64, null=True, blank=True, unique=True)
    expires_at = models.DateTimeField()
    invited_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="sent_invitations",
        blank=True,
        null=True,
    )
    delivery_pending = models.BooleanField(default=False)
    delivery_reserved_at = models.DateTimeField(blank=True, null=True)
    delivery_nonce = models.UUIDField(blank=True, null=True)
    delivery_confirmed_at = models.DateTimeField(blank=True, null=True)
    last_sent_at = models.DateTimeField(blank=True, null=True)
    accepted_at = models.DateTimeField(blank=True, null=True)
    cancelled_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "farm_invitations"
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=("farm", "pending_email"),
                name="uniq_farm_invitation_pending_email",
            ),
            models.CheckConstraint(
                condition=models.Q(role__in=FarmRole.values),
                name="farms_invitation_role_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=InvitationStatus.values),
                name="farms_invitation_status_valid",
            ),
        ]
        indexes = [
            models.Index(fields=["token_hash"], name="farms_invitation_token_idx"),
            models.Index(
                fields=["status", "expires_at"],
                name="farms_inv_stat_exp_idx",
            ),
            models.Index(
                fields=["farm", "status"],
                name="farms_inv_farm_stat_idx",
            ),
        ]
