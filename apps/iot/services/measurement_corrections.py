"""Append-only domain operations for corrections to IoT measurements."""

from decimal import Decimal, InvalidOperation
from uuid import UUID

from django.db import IntegrityError, transaction

from apps.accounts.errors import AppError
from apps.accounts.models import User
from apps.iot.models import (
    Measurement,
    MeasurementCorrection,
    MeasurementCorrectionType,
)
from apps.iot.services._common import required_text
from apps.iot.services.access import lock_manage_membership, require_read_membership

MEASUREMENT_NOT_FOUND = "medición no encontrada"
INVALID_CORRECTION_TYPE = "tipo de corrección no válido"
INVALID_CORRECTED_VALUE = "valor corregido no válido"
CORRECTION_CONFLICT = "la secuencia de correcciones cambió; vuelve a consultar el historial"
CORRECTION_REASON_REQUIRED = "el motivo de la corrección es obligatorio"
_DECIMAL_QUANTUM = Decimal("0.01")
_DECIMAL_MAX = Decimal("999.99")
_DECIMAL_MIN = Decimal("-999.99")


def list_measurement_corrections(*, user: User, farm_id: int, measurement_uid):
    """Return a farm-scoped immutable measurement and its ordered corrections."""
    require_read_membership(user=user, farm_id=farm_id)
    uid = _parse_measurement_uid(measurement_uid)
    measurement = (
        Measurement.objects.filter(farm_id=farm_id, uid=uid)
        .select_related("sensor", "frame")
        .first()
    )
    if measurement is None:
        raise AppError(MEASUREMENT_NOT_FOUND, 404)
    corrections = list(
        MeasurementCorrection.objects.filter(measurement=measurement)
        .select_related("created_by", "previous_correction")
        .order_by("revision")
    )
    measurement._prefetched_corrections = corrections
    return measurement, corrections


def create_measurement_correction(
    *,
    user: User,
    farm_id: int,
    measurement_uid,
    correction_type: str,
    corrected_value=None,
    reason: str,
) -> MeasurementCorrection:
    """Append a correction while locking the original measurement row."""
    with transaction.atomic():
        membership = lock_manage_membership(user=user, farm_id=farm_id)
        uid = _parse_measurement_uid(measurement_uid)
        measurement = (
            Measurement.objects.select_for_update()
            .filter(farm_id=membership.farm_id, uid=uid)
            .first()
        )
        if measurement is None:
            raise AppError(MEASUREMENT_NOT_FOUND, 404)

        if correction_type not in MeasurementCorrectionType.values:
            raise AppError(INVALID_CORRECTION_TYPE, 400)
        clean_reason = required_text(
            reason,
            CORRECTION_REASON_REQUIRED,
            500,
        )
        clean_value = _validate_corrected_value(corrected_value)
        if correction_type == MeasurementCorrectionType.VALUE_ADJUSTMENT:
            if clean_value is None:
                raise AppError(INVALID_CORRECTED_VALUE, 400)
        elif clean_value is not None:
            raise AppError(INVALID_CORRECTED_VALUE, 400)

        previous = (
            MeasurementCorrection.objects.filter(measurement=measurement)
            .select_for_update()
            .order_by("-revision")
            .first()
        )
        revision = previous.revision + 1 if previous else 1
        value_before = _effective_value(measurement, previous)
        try:
            with transaction.atomic():
                correction = MeasurementCorrection.objects.create(
                    measurement=measurement,
                    revision=revision,
                    previous_correction=previous,
                    previous_revision=previous.revision if previous else None,
                    value_before=value_before,
                    corrected_value=clean_value,
                    correction_type=correction_type,
                    reason=clean_reason,
                    created_by=user,
                    process_id=None,
                )
        except IntegrityError:
            raise AppError(CORRECTION_CONFLICT, 409) from None
        return correction


def _effective_value(
    measurement: Measurement, previous: MeasurementCorrection | None
) -> Decimal | None:
    if previous is None:
        return measurement.value
    if previous.correction_type == MeasurementCorrectionType.INVALIDATION:
        return None
    return previous.corrected_value


def _validate_corrected_value(value) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool) or isinstance(value, float):
        raise AppError(INVALID_CORRECTED_VALUE, 400)
    try:
        decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise AppError(INVALID_CORRECTED_VALUE, 400) from None
    if not decimal_value.is_finite():
        raise AppError(INVALID_CORRECTED_VALUE, 400)
    try:
        normalized = decimal_value.quantize(_DECIMAL_QUANTUM)
    except InvalidOperation:
        raise AppError(INVALID_CORRECTED_VALUE, 400) from None
    if normalized != decimal_value or not _DECIMAL_MIN <= normalized <= _DECIMAL_MAX:
        raise AppError(INVALID_CORRECTED_VALUE, 400)
    return normalized


def _parse_measurement_uid(value) -> UUID:
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        raise AppError(MEASUREMENT_NOT_FOUND, 404) from None
