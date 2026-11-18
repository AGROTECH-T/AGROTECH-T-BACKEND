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
    # IoT-02.2 (DEC-13): ciclo de vida de dispositivos y autorizaciones. Nunca
    # telemetría: las mediciones viven en tablas propias de IoT.
    IOT_GATEWAY_CREATED = "iot_gateway_created", "Gateway IoT registrado"
    IOT_GATEWAY_DEACTIVATED = "iot_gateway_deactivated", "Gateway IoT desactivado"
    IOT_GATEWAY_REACTIVATED = "iot_gateway_reactivated", "Gateway IoT reactivado"
    IOT_COORDINATOR_CREATED = "iot_coordinator_created", "Coordinador IoT registrado"
    IOT_COORDINATOR_DEACTIVATED = "iot_coordinator_deactivated", "Coordinador IoT desactivado"
    IOT_COORDINATOR_REACTIVATED = "iot_coordinator_reactivated", "Coordinador IoT reactivado"
    IOT_SENSOR_CREATED = "iot_sensor_created", "Sensor IoT registrado"
    IOT_SENSOR_DEACTIVATED = "iot_sensor_deactivated", "Sensor IoT desactivado"
    IOT_SENSOR_REACTIVATED = "iot_sensor_reactivated", "Sensor IoT reactivado"
    IOT_GATEWAY_AUTHORIZED = (
        "iot_gateway_authorized",
        "Coordinador IoT autorizado en gateway",
    )
    IOT_GATEWAY_AUTHORIZATION_REVOKED = (
        "iot_gateway_authorization_revoked",
        "Autorización de coordinador en gateway revocada",
    )
    # IoT-03.1: credenciales de gateway. Nunca el secreto ni su huella.
    IOT_GATEWAY_CREDENTIAL_ISSUED = (
        "iot_gateway_credential_issued",
        "Credencial de gateway emitida",
    )
    IOT_GATEWAY_CREDENTIAL_ROTATED = (
        "iot_gateway_credential_rotated",
        "Credencial de gateway rotada",
    )
    IOT_GATEWAY_CREDENTIAL_REVOKED = (
        "iot_gateway_credential_revoked",
        "Credencial de gateway revocada",
    )
    IOT_GATEWAY_CREDENTIAL_COMPROMISED = (
        "iot_gateway_credential_compromised",
        "Credencial de gateway revocada por compromiso",
    )
    IOT_GATEWAY_CREDENTIAL_CASCADE_REVOKED = (
        "iot_gateway_credential_cascade_revoked",
        "Credencial de gateway revocada por desactivación del gateway",
    )
    # IoT-03.2: credenciales de coordinador. Nunca la clave, el ciphertext ni el
    # nonce. ``iot_coordinator_credential_cascade_revoked`` mide 42 caracteres,
    # por eso ``event_type`` admite hasta 48.
    IOT_COORDINATOR_CREDENTIAL_ISSUED = (
        "iot_coordinator_credential_issued",
        "Credencial de coordinador emitida",
    )
    IOT_COORDINATOR_CREDENTIAL_ROTATED = (
        "iot_coordinator_credential_rotated",
        "Credencial de coordinador rotada",
    )
    IOT_COORDINATOR_CREDENTIAL_REVOKED = (
        "iot_coordinator_credential_revoked",
        "Credencial de coordinador revocada",
    )
    IOT_COORDINATOR_CREDENTIAL_COMPROMISED = (
        "iot_coordinator_credential_compromised",
        "Credencial de coordinador revocada por compromiso",
    )
    IOT_COORDINATOR_CREDENTIAL_CASCADE_REVOKED = (
        "iot_coordinator_credential_cascade_revoked",
        "Credencial de coordinador revocada por desactivación",
    )
    IOT_COORDINATOR_CREDENTIAL_GENERATION_EXHAUSTED = (
        "iot_coordinator_credential_generation_exhausted",
        "Generación de credencial de coordinador agotada",
    )
    # FLESPI-01: credenciales de broker (proveedor MQTT externo). Nunca el
    # secreto, su huella ni el ``token_id`` del proveedor. Confirmar (el
    # proveedor la habilitó) y activar (Django la declara vigente) son eventos
    # distintos: durante una rotación se confirma sin activar.
    IOT_BROKER_CREDENTIAL_REGISTERED = (
        "iot_broker_credential_registered",
        "Credencial de broker registrada",
    )
    IOT_BROKER_CREDENTIAL_CONFIRMED = (
        "iot_broker_credential_confirmed",
        "Credencial de broker confirmada por el proveedor",
    )
    IOT_BROKER_CREDENTIAL_ACTIVATED = (
        "iot_broker_credential_activated",
        "Credencial de broker activada",
    )
    IOT_BROKER_CREDENTIAL_REVOKED = (
        "iot_broker_credential_revoked",
        "Credencial de broker revocada",
    )


class AuditEvent(models.Model):
    """Registro inmutable de un evento de negocio."""

    event_type = models.CharField(max_length=48, choices=EventType.choices)
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
