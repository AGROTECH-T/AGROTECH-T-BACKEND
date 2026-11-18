"""Listas explícitas de campos públicos para las respuestas IoT."""
from rest_framework import serializers

from apps.iot.catalog import metric_unit
from apps.iot.models import (
    BrokerCredential, Coordinator, CoordinatorGateway, Gateway, Measurement, MeasurementCorrection,
    MeasurementCorrectionType, Sensor, SensorSource,
)


class _ClosedInputSerializer(serializers.Serializer):
    """Reject unknown fields instead of silently ignoring client supplied data."""

    def validate(self, attrs):
        unknown = set(self.initial_data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError(
                {field: "campo no permitido" for field in sorted(unknown)}
            )
        return attrs


class SensorSourceSummarySerializer(serializers.ModelSerializer):
    """Physical provenance embedded in a logical sensor response."""

    class Meta:
        model = SensorSource
        fields = ("uid", "name", "model", "manufacturer", "role", "description")
        read_only_fields = fields


class SensorSourceSerializer(serializers.ModelSerializer):
    """Public representation of a source, excluding farm and actor internals."""

    coordinator_uid = serializers.CharField(source="coordinator.uid", read_only=True)

    class Meta:
        model = SensorSource
        fields = (
            "uid",
            "coordinator_uid",
            "name",
            "model",
            "manufacturer",
            "role",
            "description",
            "is_active",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


class SensorSourceCreateSerializer(_ClosedInputSerializer):
    coordinator_uid = serializers.CharField()
    name = serializers.CharField(max_length=100)
    model = serializers.CharField(max_length=80, required=False, allow_null=True, allow_blank=True)
    manufacturer = serializers.CharField(
        max_length=100, required=False, allow_null=True, allow_blank=True
    )
    role = serializers.ChoiceField(choices=("primary", "backup_comparison"))
    description = serializers.CharField(required=False, allow_blank=True)


class SensorSourceUpdateSerializer(_ClosedInputSerializer):
    name = serializers.CharField(max_length=100, required=False)
    model = serializers.CharField(max_length=80, required=False, allow_null=True, allow_blank=True)
    manufacturer = serializers.CharField(
        max_length=100, required=False, allow_null=True, allow_blank=True
    )
    role = serializers.ChoiceField(
        choices=("primary", "backup_comparison"), required=False
    )
    description = serializers.CharField(required=False, allow_blank=True)


class SensorSourceDeactivateSerializer(_ClosedInputSerializer):
    reason = serializers.CharField(required=False, allow_blank=True, max_length=500)


class GatewaySerializer(serializers.ModelSerializer):
    """Representación pública de un gateway."""

    class Meta:
        model = Gateway
        fields = ("uid", "name", "location_label", "is_active", "created_at", "updated_at")


class BrokerCredentialSerializer(serializers.ModelSerializer):
    """Metadatos de una credencial de broker (FLESPI-01). Sin ruta HTTP todavía.

    Nunca expone ``secret_fingerprint``, ``confirmation_ref``,
    ``activation_evidence_ref``, ``correlation_id`` ni identificadores de
    proceso: lista explícita de campos, solo lectura.
    """

    class Meta:
        model = BrokerCredential
        fields = (
            "version",
            "provider",
            "state",
            "created_at",
            "confirmed_at",
            "activated_at",
            "revoked_at",
            "revocation_cause",
        )
        read_only_fields = fields


class CoordinatorSerializer(serializers.ModelSerializer):
    """Representación pública de un coordinador."""

    class Meta:
        model = Coordinator
        fields = ("uid", "name", "location_label", "is_active", "created_at", "updated_at")


class SensorSerializer(serializers.ModelSerializer):
    """Representación pública de un sensor sin columnas internas."""

    unit = serializers.SerializerMethodField()
    coordinator_uid = serializers.CharField(source="coordinator.uid", read_only=True)
    source = SensorSourceSummarySerializer(read_only=True, allow_null=True)

    class Meta:
        model = Sensor
        fields = ("uid", "channel", "metric", "unit", "is_active", "coordinator_uid", "source")

    @staticmethod
    def get_unit(sensor):
        """Deriva la unidad del catálogo fijo."""
        return metric_unit(sensor.metric)


class SensorCreateSerializer(_ClosedInputSerializer):
    """Alta de sensor (C-4). Formato y reglas de dominio los valida ``create_sensor``."""

    coordinator_uid = serializers.CharField()
    source_uid = serializers.UUIDField()
    channel = serializers.IntegerField()
    metric = serializers.CharField()


class CoordinatorGatewaySerializer(serializers.ModelSerializer):
    """Autorización coordinador-gateway, sin PK ni usuarios internos."""

    coordinator_uid = serializers.CharField(source="coordinator.uid", read_only=True)
    gateway_uid = serializers.CharField(source="gateway.uid", read_only=True)
    active = serializers.BooleanField(source="is_active", read_only=True)

    class Meta:
        model = CoordinatorGateway
        fields = ("coordinator_uid", "gateway_uid", "active", "authorized_at", "revoked_at")
        read_only_fields = fields


class GatewayAuthorizationCreateSerializer(_ClosedInputSerializer):
    """Entrada de C-1; ``authorize_gateway`` valida el formato del UID (404)."""

    gateway_uid = serializers.CharField()


class GatewayAuthorizationRevokeSerializer(_ClosedInputSerializer):
    """Entrada de C-3; el límite del motivo lo aplica el servicio (``clean_reason``)."""

    reason = serializers.CharField(required=False, allow_blank=True)


class AuthorizationHistoryQuerySerializer(serializers.Serializer):
    """Parámetros de C-2 (abierto: convive con los de paginación)."""

    include_revoked = serializers.BooleanField(required=False, default=True)


class MeasurementSerializer(serializers.ModelSerializer):
    """Representación pública de una medición, sin columnas internas."""

    sensor_uid = serializers.CharField(source="sensor.uid", read_only=True)
    unit = serializers.SerializerMethodField()
    server_received_at = serializers.DateTimeField(
        source="frame.server_received_at", read_only=True
    )
    measured_at_approximate = serializers.BooleanField(
        source="frame.measured_at_approximate", read_only=True
    )
    original_value = serializers.DecimalField(
        source="value", max_digits=5, decimal_places=2, allow_null=True, read_only=True
    )
    latest_correction = serializers.SerializerMethodField()
    effective_value = serializers.SerializerMethodField()

    class Meta:
        model = Measurement
        fields = (
            "uid",
            "sensor_uid",
            "metric",
            "value",
            "unit",
            "measured_at",
            "server_received_at",
            "measured_at_approximate",
            "original_value",
            "latest_correction",
            "effective_value",
        )

    @staticmethod
    def get_unit(measurement):
        """Deriva la unidad del catálogo fijo."""
        return metric_unit(measurement.metric)

    @staticmethod
    def _corrections(measurement):
        return getattr(measurement, "_prefetched_corrections", None)

    def get_latest_correction(self, measurement):
        corrections = self._corrections(measurement)
        latest = corrections[-1] if corrections else None
        return CorrectionSummarySerializer(latest).data if latest else None

    def get_effective_value(self, measurement):
        corrections = self._corrections(measurement)
        if not corrections:
            return measurement.value
        latest = corrections[-1]
        if latest.correction_type == MeasurementCorrectionType.INVALIDATION:
            return None
        return latest.corrected_value


class CorrectionSummarySerializer(serializers.ModelSerializer):
    actor = serializers.SerializerMethodField()

    class Meta:
        model = MeasurementCorrection
        fields = (
            "uid", "revision", "correction_type", "value_before",
            "corrected_value", "reason", "created_at", "actor",
        )
        read_only_fields = fields

    @staticmethod
    def get_actor(correction):
        if correction.created_by_id is None:
            return "process"
        return {
            "type": "user",
            "name": f"{correction.created_by.first_name} {correction.created_by.last_name}".strip(),
        }


class MeasurementCorrectionSerializer(serializers.ModelSerializer):
    actor = serializers.SerializerMethodField()

    class Meta:
        model = MeasurementCorrection
        fields = (
            "uid", "revision", "previous_revision", "value_before",
            "corrected_value", "correction_type", "reason", "created_at", "actor",
        )
        read_only_fields = fields

    @staticmethod
    def get_actor(correction):
        return CorrectionSummarySerializer.get_actor(correction)


class MeasurementCorrectionCreateSerializer(_ClosedInputSerializer):
    correction_type = serializers.ChoiceField(
        choices=(MeasurementCorrectionType.VALUE_ADJUSTMENT, MeasurementCorrectionType.INVALIDATION)
    )
    corrected_value = serializers.JSONField(required=False, allow_null=True)
    reason = serializers.CharField(max_length=500, allow_blank=False, trim_whitespace=True)

    def validate_corrected_value(self, value):
        if value is None:
            return None
        if isinstance(value, bool) or isinstance(value, float) or not isinstance(value, (str, int)):
            raise serializers.ValidationError("use una cadena decimal exacta con hasta dos decimales")
        from decimal import Decimal, InvalidOperation
        try:
            result = Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            raise serializers.ValidationError("valor decimal no válido")
        if not result.is_finite() or result.as_tuple().exponent < -2:
            raise serializers.ValidationError("use hasta dos decimales")
        return str(result)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if attrs["correction_type"] == MeasurementCorrectionType.VALUE_ADJUSTMENT:
            if attrs.get("corrected_value") is None:
                raise serializers.ValidationError({"corrected_value": "campo obligatorio"})
        elif attrs.get("corrected_value") is not None:
            raise serializers.ValidationError({"corrected_value": "debe ser null para invalidación"})
        return attrs
