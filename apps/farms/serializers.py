"""Contratos HTTP del dominio de fincas.

Propósito: validar la creación y documentar la respuesta pública de una finca.
Contexto: el rol se expone con su valor interno del catálogo FarmRole.
"""
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.farms.capabilities import farm_capabilities
from apps.farms.models import FarmRole
from apps.trazabilidad.services import AUDIT_REASON_MAX_LENGTH, REASON_TOO_LONG


class FarmCreateSerializer(serializers.Serializer):
    """Valida el único dato editable al crear o actualizar una finca."""

    name = serializers.CharField(max_length=100, allow_blank=False)

    def to_internal_value(self, data):
        """Rechaza los campos que no pertenecen al contrato."""
        if hasattr(data, "keys"):
            extra_fields = sorted(set(data.keys()) - set(self.fields))
            if extra_fields:
                raise serializers.ValidationError(
                    {field: "Campo no permitido." for field in extra_fields}
                )
        return super().to_internal_value(data)


FarmUpdateSerializer = FarmCreateSerializer


class FarmCapabilitiesSerializer(serializers.Serializer):
    """Documenta las capacidades calculadas por el servidor (solo lectura)."""

    can_edit_farm = serializers.BooleanField(read_only=True)
    can_deactivate_farm = serializers.BooleanField(read_only=True)
    can_reactivate_farm = serializers.BooleanField(read_only=True)
    can_view_history = serializers.BooleanField(read_only=True)
    can_manage_members = serializers.BooleanField(read_only=True)
    can_manage_invitations = serializers.BooleanField(read_only=True)
    can_read_inventory = serializers.BooleanField(read_only=True)
    can_write_inventory = serializers.BooleanField(read_only=True)


class FarmResponseSerializer(serializers.Serializer):
    """Expone la finca, el rol del usuario autenticado y sus capacidades."""

    id = serializers.IntegerField(source="farm_id")
    name = serializers.CharField(source="farm.name")
    role = serializers.ChoiceField(choices=FarmRole.choices)
    created_at = serializers.DateTimeField(source="farm.created_at")
    updated_at = serializers.DateTimeField(source="farm.updated_at")
    is_active = serializers.BooleanField(source="farm.is_active")
    deactivated_at = serializers.DateTimeField(
        source="farm.deactivated_at",
        allow_null=True,
    )
    capabilities = serializers.SerializerMethodField()

    @extend_schema_field(FarmCapabilitiesSerializer)
    def get_capabilities(self, membership) -> dict:
        """Capacidades de la cuenta en esta finca, calculadas en el servidor."""
        return farm_capabilities(membership)


class AuditEventSerializer(serializers.Serializer):
    """Expone los campos autorizados de un evento de auditoría.

    No incluye identificadores internos, relaciones ni metadatos técnicos.
    """

    event_type = serializers.CharField()
    event_type_label = serializers.CharField(source="get_event_type_display")
    created_at = serializers.DateTimeField()
    actor_display = serializers.CharField()
    entity_type = serializers.CharField()
    entity_id = serializers.CharField()
    before_data = serializers.JSONField(allow_null=True)
    after_data = serializers.JSONField(allow_null=True)
    reason = serializers.CharField()


class HistoryPageSerializer(serializers.Serializer):
    """Documenta la respuesta paginada del historial."""

    count = serializers.IntegerField()
    next = serializers.CharField(allow_null=True)
    previous = serializers.CharField(allow_null=True)
    results = AuditEventSerializer(many=True)


class FarmMemberSerializer(serializers.Serializer):
    """Expone la información pública mínima de un integrante."""

    id = serializers.IntegerField()
    name = serializers.SerializerMethodField()
    role = serializers.CharField()
    status = serializers.CharField()
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()

    def get_name(self, obj) -> str:
        user = obj.user
        return " ".join(part for part in (user.first_name, user.last_name) if part).strip()


class ChangeMemberRoleSerializer(serializers.Serializer):
    """Valida el cuerpo de cambio de rol."""

    role = serializers.ChoiceField(choices=FarmRole.choices)


class RevokeMemberSerializer(serializers.Serializer):
    """Valida el cuerpo opcional de revocación.

    El motivo se recorta y no puede superar el límite de AuditEvent.reason;
    se rechaza antes de iniciar la operación, nunca se trunca.
    """

    reason = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        max_length=AUDIT_REASON_MAX_LENGTH,
        error_messages={"max_length": REASON_TOO_LONG},
    )


class TransferResponseSerializer(serializers.Serializer):
    """Identifica al administrador anterior y al nuevo tras una transferencia."""

    previous_admin = FarmMemberSerializer()
    new_admin = FarmMemberSerializer()


class MemberPageSerializer(serializers.Serializer):
    """Documenta la respuesta paginada del listado de integrantes."""

    count = serializers.IntegerField()
    next = serializers.CharField(allow_null=True)
    previous = serializers.CharField(allow_null=True)
    results = FarmMemberSerializer(many=True)


class InvitationCreateSerializer(serializers.Serializer):
    """Valida el correo y el rol al crear una invitación."""

    email = serializers.EmailField()
    role = serializers.ChoiceField(choices=FarmRole.choices)

    def to_internal_value(self, data):
        """Rechaza los campos que no pertenecen al contrato."""
        if hasattr(data, "keys"):
            extra_fields = sorted(set(data.keys()) - set(self.fields))
            if extra_fields:
                raise serializers.ValidationError(
                    {field: "Campo no permitido." for field in extra_fields}
                )
        return super().to_internal_value(data)

    def validate_email(self, value: str) -> str:
        """Normaliza el correo del destinatario."""
        return value.strip().lower()


class InvitationRoleSerializer(serializers.Serializer):
    """Valida el nuevo rol de una invitación."""

    role = serializers.ChoiceField(choices=FarmRole.choices)


class InvitationAcceptSerializer(serializers.Serializer):
    """Valida el token de aceptación."""

    token = serializers.CharField(trim_whitespace=False)


class InvitationResponseSerializer(serializers.Serializer):
    """Expone los campos autorizados de una invitación (sin token)."""

    id = serializers.IntegerField()
    email = serializers.EmailField()
    role = serializers.CharField()
    status = serializers.CharField()
    expires_at = serializers.DateTimeField()
    last_sent_at = serializers.DateTimeField(allow_null=True)
    delivery_pending = serializers.BooleanField()
    accepted_at = serializers.DateTimeField(allow_null=True)
    cancelled_at = serializers.DateTimeField(allow_null=True)
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()


class InvitationCodeSerializer(serializers.Serializer):
    """Valida el cuerpo de previsualizar y aceptar por código (F10.3.3-B).

    Solo admite ``code``. La identidad y el correo salen de la cuenta
    autenticada, así que cualquier otro campo (por ejemplo ``email``) se
    rechaza. El formato del código lo valida ``invitation_codes`` en el
    servicio, para que un código mal formado responda con el 404 genérico.
    """

    code = serializers.CharField(trim_whitespace=False, max_length=64)

    def to_internal_value(self, data):
        """Rechaza los campos que no pertenecen al contrato."""
        if hasattr(data, "keys"):
            extra_fields = sorted(set(data.keys()) - set(self.fields))
            if extra_fields:
                raise serializers.ValidationError(
                    {field: "Campo no permitido." for field in extra_fields}
                )
        return super().to_internal_value(data)


class InvitationCodePreviewSerializer(serializers.Serializer):
    """Lo mínimo para confirmar: finca, rol ofrecido y expiración."""

    farm_name = serializers.CharField()
    role = serializers.CharField()
    expires_at = serializers.DateTimeField()


class InvitationAcceptResponseSerializer(serializers.Serializer):
    """Identifica la membresía resultante de la aceptación."""

    farm_id = serializers.IntegerField()
    membership_id = serializers.IntegerField()
    role = serializers.CharField()
    status = serializers.CharField()
