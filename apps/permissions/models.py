"""Modelos del dominio de permisos configurables.

Propósito: persistir asignaciones y restricciones de permisos operativos por
membresía, y las políticas de aprobación por finca y módulo.

Contexto: el catálogo de permisos vive en ``catalog.py``; estos modelos guardan
únicamente las decisiones de negocio (asignado/restricto/política). Las
restricciones solo existen mientras están activas; su retirada se auditará vía
``AuditEvent`` en una etapa posterior.
"""
from django.db import models


class AssignmentSource(models.TextChoices):
    """Origen de una asignación de permiso."""

    INITIALIZATION = "initialization", "Inicialización de finca"
    ADMIN = "admin", "Asignación administrativa"
    INVITATION = "invitation", "Invitación aceptada"
    MIGRATION = "migration", "Migración de datos"


class PermissionAssignment(models.Model):
    """Asignación explícita de un permiso a una membresía."""

    membership = models.ForeignKey(
        "farms.FarmMembership",
        on_delete=models.CASCADE,
        related_name="permission_assignments",
    )
    module = models.CharField(max_length=64)
    action = models.CharField(max_length=64)
    source = models.CharField(max_length=20, choices=AssignmentSource.choices)
    migration_batch = models.UUIDField(blank=True, null=True)
    granted_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        related_name="granted_permission_assignments",
        blank=True,
        null=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "permission_assignments"
        constraints = [
            models.UniqueConstraint(
                fields=["membership", "module", "action"],
                name="uniq_permission_assignment",
            ),
        ]


class PermissionRestriction(models.Model):
    """Restricción activa de un permiso para una membresía."""

    membership = models.ForeignKey(
        "farms.FarmMembership",
        on_delete=models.CASCADE,
        related_name="permission_restrictions",
    )
    module = models.CharField(max_length=64)
    action = models.CharField(max_length=64)
    restricted_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        related_name="restricted_permissions",
        blank=True,
        null=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "permission_restrictions"
        constraints = [
            models.UniqueConstraint(
                fields=["membership", "module", "action"],
                name="uniq_permission_restriction",
            ),
        ]


class ApprovalPolicy(models.Model):
    """Política de aprobación de un módulo dentro de una finca."""

    farm = models.ForeignKey(
        "farms.Farm",
        on_delete=models.CASCADE,
        related_name="approval_policies",
    )
    module = models.CharField(max_length=64)
    required = models.BooleanField(default=False)
    self_approval_allowed = models.BooleanField(default=False)
    updated_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        related_name="updated_approval_policies",
        blank=True,
        null=True,
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "approval_policies"
        constraints = [
            models.UniqueConstraint(
                fields=["farm", "module"],
                name="uniq_approval_policy",
            ),
        ]
