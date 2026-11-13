"""Modelo centralizado de auditoría de AGROTECH-T.

Propósito: conservar eventos inmutables de las operaciones de negocio con
identificación histórica estable del actor y de la finca, incluso si la cuenta
o la finca se eliminan más adelante.

Contexto: los eventos se escriben dentro de la misma transacción que la
operación auditada y son append-only para el funcionamiento ordinario.
"""

from django.core.exceptions import ValidationError
from django.db import models


class EventType(models.TextChoices):
    """Catálogo de eventos de Farms, membresías y cuentas."""

    FARM_CREATED = "farm_created", "Finca creada"
    FARM_UPDATED = "farm_updated", "Finca actualizada"
    FARM_DEACTIVATED = "farm_deactivated", "Finca desactivada"
    FARM_REACTIVATED = "farm_reactivated", "Finca reactivada"
    MEMBER_ROLE_CHANGED = "member_role_changed", "Rol de integrante cambiado"
    MEMBER_REVOKED = "member_revoked", "Integrante revocado"
    MEMBER_LEFT = "member_left", "Integrante abandonó la finca"
    ADMIN_TRANSFERRED = "admin_transferred", "Administración transferida"
    EMAIL_VERIFIED = "email_verified", "Correo verificado"
    EMAIL_CHANGED = "email_changed", "Correo cambiado"
    PERMISSION_GRANTED = "permission_granted", "Permiso concedido"
    PERMISSION_REVOKED = "permission_revoked", "Permiso revocado"
    PERMISSION_RESTRICTED = "permission_restricted", "Permiso restringido"
    PERMISSION_UNRESTRICTED = "permission_unrestricted", "Restricción retirada"
    SESSION_OPENED = "session_opened", "Sesión abierta"
    SESSION_CLOSED = "session_closed", "Sesión cerrada"
    INVITATION_CREATED = "invitation_created", "Invitación creada"
    INVITATION_RESEND_STARTED = "invitation_resend_started", "Reenvío de invitación iniciado"
    INVITATION_RESENT = "invitation_resent", "Invitación reenviada"
    INVITATION_DELIVERY_FAILED = "invitation_delivery_failed", "Entrega de invitación fallida"
    INVITATION_ROLE_CHANGED = "invitation_role_changed", "Rol de invitación cambiado"
    INVITATION_CANCELLED = "invitation_cancelled", "Invitación cancelada"
    INVITATION_EXPIRED = "invitation_expired", "Invitación expirada"
    INVITATION_ACCEPTED = "invitation_accepted", "Invitación aceptada"
    MEMBER_JOINED = "member_joined", "Integrante incorporado"
    MEMBER_REACTIVATED = "member_reactivated", "Integrante reactivado"
    FARM_CREATION_GRANTED = "farm_creation_granted", "Capacidad de crear fincas concedida"
    FARM_CREATION_REVOKED = "farm_creation_revoked", "Capacidad de crear fincas retirada"


class AuditEvent(models.Model):
    """Registro inmutable de un evento de negocio."""

    event_type = models.CharField(max_length=40, choices=EventType.choices)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    farm = models.ForeignKey(
        "farms.Farm",
        on_delete=models.SET_NULL,
        related_name="audit_events",
        blank=True,
        null=True,
    )
    farm_identifier = models.BigIntegerField(blank=True, null=True)

    actor = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        related_name="audit_events",
        blank=True,
        null=True,
    )
    actor_identifier = models.BigIntegerField(blank=True, null=True)
    actor_display = models.CharField(max_length=80, blank=True, default="")

    entity_type = models.CharField(max_length=40)
    entity_id = models.CharField(max_length=64)

    before_data = models.JSONField(blank=True, null=True)
    after_data = models.JSONField(blank=True, null=True)
    reason = models.CharField(max_length=200, blank=True, default="")
    metadata = models.JSONField(blank=True, default=dict)

    class Meta:
        db_table = "audit_events"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["farm", "created_at"], name="audit_farm_created_idx"),
            models.Index(
                fields=["entity_type", "entity_id", "created_at"],
                name="audit_entity_created_idx",
            ),
            models.Index(fields=["event_type"], name="audit_type_idx"),
            models.Index(fields=["actor", "created_at"], name="audit_actor_created_idx"),
        ]

    def save(self, *args, **kwargs) -> None:
        """Bloquea la modificación ordinaria de un evento ya persistido."""
        if self.pk is not None:
            raise ValidationError(
                "Los eventos de auditoría son append-only: no pueden modificarse."
            )
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs) -> None:
        """Bloquea la eliminación ordinaria de un evento."""
        raise ValidationError("Los eventos de auditoría son append-only: no pueden eliminarse.")

    def __str__(self) -> str:
        return f"{self.event_type} #{self.pk}"
