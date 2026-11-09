"""Pruebas del modelo de auditoría."""

import pytest
from django.core.exceptions import ValidationError

from apps.trazabilidad.models import AuditEvent, EventType

pytestmark = pytest.mark.django_db


def test_event_catalog_is_exact():
    assert EventType.values == [
        "farm_created",
        "farm_updated",
        "farm_deactivated",
        "member_role_changed",
        "member_revoked",
        "member_left",
        "admin_transferred",
        "email_verified",
        "email_changed",
        "permission_granted",
        "permission_revoked",
        "permission_restricted",
        "permission_unrestricted",
        "session_opened",
        "session_closed",
    ]


def test_event_is_append_only_on_update():
    event = AuditEvent.objects.create(
        event_type=EventType.FARM_CREATED,
        entity_type="farm",
        entity_id="1",
    )
    event.reason = "intento de modificación"
    with pytest.raises(ValidationError, match="append-only"):
        event.save()


def test_event_is_append_only_on_delete():
    event = AuditEvent.objects.create(
        event_type=EventType.FARM_CREATED,
        entity_type="farm",
        entity_id="1",
    )
    with pytest.raises(ValidationError, match="append-only"):
        event.delete()
