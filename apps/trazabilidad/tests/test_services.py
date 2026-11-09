"""Pruebas del servicio central de auditoría."""
import pytest
from django.contrib.auth.hashers import make_password
from django.core.exceptions import ValidationError

from apps.accounts.models import User
from apps.farms.models import Farm
from apps.trazabilidad.models import EventType
from apps.trazabilidad.services import record_audit_event

pytestmark = pytest.mark.django_db


def _make_user(identification="11111111") -> User:
    return User.objects.create(
        first_name="Ana",
        last_name="Pérez",
        identification=identification,
        password_hash=make_password("Secure1!"),
    )


def _make_farm(name="La Esperanza") -> Farm:
    return Farm.objects.create(name=name)


def test_record_creates_event_with_snapshots():
    user = _make_user()
    farm = _make_farm()
    event = record_audit_event(
        farm=farm,
        actor=user,
        event_type=EventType.FARM_CREATED,
        entity_type="farm",
        entity_id=farm.pk,
        after={"name": farm.name},
    )
    assert event.pk is not None
    assert event.farm_id == farm.pk
    assert event.farm_identifier == farm.pk
    assert event.actor_id == user.pk
    assert event.actor_identifier == user.pk
    assert event.actor_display == "Ana Pérez"
    assert event.entity_type == "farm"
    assert event.entity_id == str(farm.pk)
    assert event.after_data == {"name": farm.name}


def test_record_rejects_unknown_event_type():
    with pytest.raises(ValidationError, match="tipo de evento"):
        record_audit_event(event_type="nope", entity_type="farm", entity_id=1)


def test_record_requires_entity():
    with pytest.raises(ValidationError, match="obligatorios"):
        record_audit_event(
            event_type=EventType.FARM_CREATED,
            entity_type="",
            entity_id="",
        )


def test_record_rejects_sensitive_keys():
    with pytest.raises(ValidationError, match="clave sensible"):
        record_audit_event(
            event_type=EventType.FARM_CREATED,
            entity_type="farm",
            entity_id=1,
            after={"password": "x"},
        )


def test_record_rejects_non_serializable_payload():
    with pytest.raises(ValidationError, match="serializable"):
        record_audit_event(
            event_type=EventType.FARM_CREATED,
            entity_type="farm",
            entity_id=1,
            after={"name": object()},
        )


def test_record_rejects_non_dict_payload():
    with pytest.raises(ValidationError, match="objeto JSON"):
        record_audit_event(
            event_type=EventType.FARM_CREATED,
            entity_type="farm",
            entity_id=1,
            after="not-a-dict",
        )


@pytest.mark.parametrize(
    "key",
    [
        "password", "Password", "PASSWORD",
        "api-key", "api_key", "api key",
        "refresh-token", "refresh_token",
        "secret.key", "access-token",
        "email", "correo", "phone", "telefono",
        "document", "documento", "otp", "cookie",
        "user_password",
    ],
)
def test_record_rejects_sensitive_key_variants(key):
    with pytest.raises(ValidationError, match="clave sensible"):
        record_audit_event(
            event_type=EventType.FARM_CREATED,
            entity_type="farm",
            entity_id=1,
            after={key: "valor"},
        )


def test_record_rejects_nested_list_of_dicts():
    payload = {"metadata": {"items": [{"password": "valor"}]}}
    with pytest.raises(ValidationError, match="clave sensible"):
        record_audit_event(
            event_type=EventType.FARM_CREATED,
            entity_type="farm",
            entity_id=1,
            after=payload,
        )


def test_record_rejects_tuple_of_dicts():
    payload = {"records": ({"user": {"access_token": "valor"}},)}
    with pytest.raises(ValidationError, match="clave sensible"):
        record_audit_event(
            event_type=EventType.FARM_CREATED,
            entity_type="farm",
            entity_id=1,
            after=payload,
        )


def test_record_rejects_deep_mixed_structures():
    payload = {"a": [{"b": ({"c": [{"secret_key": 1}]},)}]}
    with pytest.raises(ValidationError, match="clave sensible"):
        record_audit_event(
            event_type=EventType.FARM_CREATED,
            entity_type="farm",
            entity_id=1,
            after=payload,
        )


def test_record_rejects_cyclic_structure():
    payload = {}
    payload["self"] = payload
    with pytest.raises(ValidationError, match="cíclica"):
        record_audit_event(
            event_type=EventType.FARM_CREATED,
            entity_type="farm",
            entity_id=1,
            after=payload,
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "Fertilizante"},
        {"quantity": 5, "min_stock": 0, "unit": "kg", "category": "concentrados"},
        {"note": "compra", "is_active": True},
        {"barcode": "770001"},
    ],
)
def test_record_accepts_innocuous_fields(payload):
    event = record_audit_event(
        event_type=EventType.FARM_CREATED,
        entity_type="farm",
        entity_id=1,
        after=payload,
    )
    assert event.after_data == payload


def test_record_accepts_nested_legitimate_structures():
    payload = {"items": [{"name": "A"}, {"name": "B"}], "tags": ["x", "y"]}
    event = record_audit_event(
        event_type=EventType.FARM_CREATED,
        entity_type="farm",
        entity_id=1,
        after=payload,
    )
    assert event.after_data == payload
