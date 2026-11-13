# ruff: noqa: S101, S105, S106
"""Pruebas de F10.3.2: códigos integrados en los servicios de invitación.

Propósito: comprobar que crear, reenviar y cambiar el rol generan una huella
nueva; que cancelar, aceptar y expirar la vacían junto con ``token_hash``; los
reintentos ante colisión de ``code_hash``; la validación de la clave y que ni
el código ni la huella aparecen en respuestas, auditoría ni logs.
Contexto: solo en la base desechable del orquestador. La clave HMAC es
ficticia y se genera en cada prueba (``conftest.token_key``). El correo se
sustituye por un doble: no se envía nada.
"""
import json
import logging
import secrets
from datetime import timedelta
from io import StringIO
from pathlib import Path

import pytest
from django.conf import settings as django_settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.management import call_command
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from apps.accounts.errors import AppError
from apps.accounts.integrations.delivery import DeliveryOutcome
from apps.farms import invitation_codes, invitation_services, services
from apps.farms.models import (
    Farm,
    FarmInvitation,
    FarmMembership,
    FarmRole,
    InvitationStatus,
    MembershipStatus,
)
from apps.trazabilidad.models import AuditEvent, EventType
from apps.trazabilidad.services import record_audit_event
from config.invitation_code_key import (
    check_invitation_code_key,
    resolve_invitation_code_key,
)

pytestmark = pytest.mark.django_db

GUEST = "invitada@example.com"
TOKEN = "token-de-enlace-f1032"


@pytest.fixture(autouse=True)
def no_mail(monkeypatch):
    """Sustituye la entrega: ninguna prueba envía correos."""
    sent = []

    def fake_send(**kwargs):
        sent.append(kwargs)
        return DeliveryOutcome.ACCEPTED

    monkeypatch.setattr(invitation_services, "_send_invitation", fake_send)
    return sent


@pytest.fixture
def issued(monkeypatch):
    """Registra cada (correo, código, huella) que generan los servicios."""
    seen = []
    real = invitation_codes.new_code_for

    def spy(email):
        code, digest = real(email)
        seen.append((email, code, digest))
        return code, digest

    monkeypatch.setattr(invitation_codes, "new_code_for", spy)
    return seen


@pytest.fixture
def fixed_token(monkeypatch):
    monkeypatch.setattr(invitation_services, "_new_token", lambda: TOKEN)
    return TOKEN


@pytest.fixture
def admin(make_user):
    return make_user("81000001")


@pytest.fixture
def farm_id(admin):
    return services.create_farm(user=admin, name="Finca F1032").farm_id


def _create(admin, farm_id, email=GUEST, role=FarmRole.OPERADOR):
    return invitation_services.create_invitation(
        user=admin, farm_id=farm_id, email=email, role=role
    )


def _events(farm_id, event_type):
    return AuditEvent.objects.filter(farm_id=farm_id, event_type=event_type).count()


def _secret_forms(issued):
    """Códigos (con y sin guion) y huellas emitidos en la prueba."""
    forms = set()
    for _email, code, digest in issued:
        forms.update({code, code.replace("-", ""), digest})
    return forms


def _raw_row(invitation_id) -> list:
    with connection.cursor() as cursor:
        cursor.execute("SELECT * FROM farm_invitations WHERE id = %s", [invitation_id])
        return [str(value) for value in cursor.fetchone()]


def _sequence(monkeypatch, pairs):
    """Hace que new_code_for devuelva los pares dados, en orden."""
    calls = []
    remaining = list(pairs)

    def fake(email):
        calls.append(email)
        return remaining.pop(0)

    monkeypatch.setattr(invitation_codes, "new_code_for", fake)
    return calls


def _occupy(digest):
    """Guarda una invitación ajena con la huella dada (para forzar colisión)."""
    other = Farm.objects.create(name="Finca ajena")
    return FarmInvitation.objects.create(
        farm=other,
        email="ajena@example.com",
        pending_email="ajena@example.com",
        role=FarmRole.CONSULTA,
        token_hash="",
        code_hash=digest,
        expires_at=timezone.now() + timedelta(days=7),
    )


def _no_inactive_hashes():
    return not FarmInvitation.objects.exclude(status=InvitationStatus.PENDING).filter(
        code_hash__isnull=False
    ).exists()


# --- Creación -----------------------------------------------------------------

def test_create_generates_code_hash_bound_to_email(admin, farm_id, issued):
    invitation = _create(admin, farm_id, email="  Invitada@Example.COM ")
    invitation.refresh_from_db()
    assert len(issued) == 1
    email, code, digest = issued[0]
    assert email == GUEST
    assert invitation.code_hash == digest
    assert len(invitation.code_hash) == 64
    expected = invitation_codes.code_hash(GUEST, code)
    assert invitation_codes.hashes_match(invitation.code_hash, expected)
    assert invitation.code_hash != invitation_codes.code_hash("otra@example.com", code)
    assert invitation.token_hash and invitation.token_hash != invitation.code_hash


def test_create_does_not_store_plain_code(admin, farm_id, issued):
    invitation = _create(admin, farm_id)
    _email, code, _digest = issued[0]
    row = " ".join(_raw_row(invitation.pk)).upper()
    assert code not in row
    assert code.replace("-", "") not in row


def test_api_never_returns_code_or_hash(auth_client, user, issued):
    farm = auth_client.post("/api/v1/farms/", {"name": "Finca API"}, format="json")
    farm_id = farm.data["id"]
    create = auth_client.post(
        f"/api/v1/farms/{farm_id}/invitations/",
        {"email": GUEST, "role": "operador"},
        format="json",
    )
    assert create.status_code == 201
    invitation_id = create.data["id"]
    responses = [
        create,
        auth_client.get(f"/api/v1/farms/{farm_id}/invitations/"),
        auth_client.post(f"/api/v1/farms/{farm_id}/invitations/{invitation_id}/resend/"),
        auth_client.patch(
            f"/api/v1/farms/{farm_id}/invitations/{invitation_id}/",
            {"role": "consulta"},
            format="json",
        ),
    ]
    assert [r.status_code for r in responses] == [201, 200, 200, 200]
    assert set(create.data) == {
        "id", "email", "role", "status", "expires_at", "last_sent_at",
        "delivery_pending", "accepted_at", "cancelled_at", "created_at", "updated_at",
    }
    secrets_seen = _secret_forms(issued)
    assert len(issued) == 3
    for response in responses:
        body = response.content.decode()
        assert "code" not in body.lower()
        for secret in secrets_seen:
            assert secret not in body.upper() and secret not in body


def test_missing_key_fails_closed_without_side_effects(settings, admin, farm_id):
    settings.INVITATION_CODE_HMAC_KEY = ""
    with pytest.raises(ImproperlyConfigured, match="INVITATION_CODE_HMAC_KEY"):
        _create(admin, farm_id)
    assert not FarmInvitation.objects.filter(farm_id=farm_id).exists()
    assert _events(farm_id, EventType.INVITATION_CREATED) == 0


def test_fictitious_test_key_works(settings, admin, farm_id):
    settings.INVITATION_CODE_HMAC_KEY = secrets.token_urlsafe(48)
    invitation = _create(admin, farm_id)
    assert invitation.code_hash and len(invitation.code_hash) == 64


# --- Reenvío y cambio de rol -----------------------------------------------------

def test_resend_replaces_hash_renews_expiry_and_nonce(admin, farm_id, issued, no_mail):
    invitation = _create(admin, farm_id)
    invitation.refresh_from_db()
    old_hash, old_token = invitation.code_hash, invitation.token_hash
    old_nonce, old_expiry = invitation.delivery_nonce, invitation.expires_at
    FarmInvitation.objects.filter(pk=invitation.pk).update(
        expires_at=timezone.now() + timedelta(days=1)
    )

    invitation_services.resend_invitation(
        user=admin, farm_id=farm_id, invitation_id=invitation.pk
    )

    invitation.refresh_from_db()
    assert len(issued) == 2
    assert invitation.code_hash == issued[1][2]
    assert not invitation_codes.hashes_match(invitation.code_hash, old_hash)
    assert not FarmInvitation.objects.filter(code_hash=old_hash).exists()
    assert invitation.token_hash != old_token
    assert invitation.delivery_nonce is not None and invitation.delivery_nonce != old_nonce
    assert invitation.delivery_pending is False
    assert invitation.delivery_confirmed_at is not None
    assert invitation.expires_at >= old_expiry
    assert invitation.expires_at > timezone.now() + timedelta(days=6, hours=23)
    assert invitation.status == InvitationStatus.PENDING
    assert len(no_mail) == 2


def test_late_delivery_of_previous_nonce_is_ignored(admin, farm_id, monkeypatch):
    invitation = _create(admin, farm_id)
    stale = FarmInvitation.objects.get(pk=invitation.pk)
    invitation_services.resend_invitation(
        user=admin, farm_id=farm_id, invitation_id=invitation.pk
    )
    current = FarmInvitation.objects.get(pk=invitation.pk)
    FarmInvitation.objects.filter(pk=invitation.pk).update(delivery_pending=True)
    invitation_services._finalize_delivery(
        invitation=stale, farm=current.farm, actor=admin, code="ABCDE-FGHJK"
    )
    current.refresh_from_db()
    assert current.delivery_pending is True  # el nonce antiguo no se aplicó
    assert current.code_hash != stale.code_hash


def test_change_role_replaces_hash_and_keeps_new_role(admin, farm_id, issued):
    invitation = _create(admin, farm_id)
    invitation.refresh_from_db()
    old_hash, old_token, old_nonce = (
        invitation.code_hash, invitation.token_hash, invitation.delivery_nonce
    )

    invitation_services.change_invitation_role(
        user=admin, farm_id=farm_id, invitation_id=invitation.pk, role=FarmRole.CONSULTA
    )

    invitation.refresh_from_db()
    assert invitation.role == FarmRole.CONSULTA
    assert invitation.code_hash == issued[1][2]
    assert not invitation_codes.hashes_match(invitation.code_hash, old_hash)
    assert not FarmInvitation.objects.filter(code_hash=old_hash).exists()
    assert invitation.token_hash != old_token
    assert invitation.delivery_nonce != old_nonce
    assert invitation.expires_at > timezone.now() + timedelta(days=6, hours=23)


def test_change_role_rejects_invalid_role_without_new_code(admin, farm_id, issued):
    invitation = _create(admin, farm_id)
    with pytest.raises(AppError) as info:
        invitation_services.change_invitation_role(
            user=admin, farm_id=farm_id, invitation_id=invitation.pk, role="dueno"
        )
    assert info.value.status_code == 400
    assert len(issued) == 1
    invitation.refresh_from_db()
    assert invitation.code_hash == issued[0][2]


# --- Cancelación, aceptación y expiración -----------------------------------------

def test_cancel_clears_hash_token_and_delivery_data(admin, farm_id):
    invitation = _create(admin, farm_id)
    invitation_services.cancel_invitation(
        user=admin, farm_id=farm_id, invitation_id=invitation.pk
    )
    invitation.refresh_from_db()
    assert invitation.status == InvitationStatus.CANCELLED
    assert invitation.code_hash is None
    assert invitation.token_hash == ""
    assert invitation.pending_email is None
    assert invitation.delivery_pending is False
    assert invitation.delivery_reserved_at is None
    assert invitation.delivery_nonce is None
    assert _events(farm_id, EventType.INVITATION_CANCELLED) == 1
    assert _no_inactive_hashes()


def test_legacy_link_acceptance_clears_hash_and_token(admin, farm_id, make_user, fixed_token):
    guest = make_user("81000002", correo=GUEST)
    invitation = _create(admin, farm_id, role=FarmRole.CONSULTA)
    assert invitation.code_hash

    membership = invitation_services.accept_invitation(user=guest, token=fixed_token)

    invitation.refresh_from_db()
    assert membership.role == FarmRole.CONSULTA
    assert membership.status == MembershipStatus.ACTIVE
    assert invitation.status == InvitationStatus.ACCEPTED
    assert invitation.code_hash is None
    assert invitation.token_hash == ""
    assert _events(farm_id, EventType.INVITATION_ACCEPTED) == 1
    assert _events(farm_id, EventType.MEMBER_JOINED) == 1
    with pytest.raises(AppError) as again:
        invitation_services.accept_invitation(user=guest, token=fixed_token)
    assert again.value.status_code == 404


def test_link_acceptance_reactivates_membership_and_clears_hash(
    admin, farm_id, make_user, fixed_token
):
    guest = make_user("81000003", correo=GUEST)
    previous = FarmMembership.objects.create(
        farm_id=farm_id,
        user=guest,
        role=FarmRole.OPERADOR,
        status=MembershipStatus.LEFT,
        left_at=timezone.now(),
    )
    _create(admin, farm_id, role=FarmRole.CONSULTA)

    membership = invitation_services.accept_invitation(user=guest, token=fixed_token)

    assert membership.pk == previous.pk
    assert membership.status == MembershipStatus.ACTIVE
    assert membership.role == FarmRole.CONSULTA
    assert _events(farm_id, EventType.MEMBER_REACTIVATED) == 1
    assert _no_inactive_hashes()


def test_pre_f1032_invitation_without_code_is_still_accepted(admin, farm_id, make_user):
    guest = make_user("81000004", correo=GUEST)
    legacy = FarmInvitation.objects.create(
        farm_id=farm_id,
        email=GUEST,
        pending_email=GUEST,
        role=FarmRole.OPERADOR,
        token_hash=invitation_services._hash_token("enlace-antiguo"),
        expires_at=timezone.now() + timedelta(days=3),
    )
    assert legacy.code_hash is None
    invitation_services.accept_invitation(user=guest, token="enlace-antiguo")
    legacy.refresh_from_db()
    assert legacy.status == InvitationStatus.ACCEPTED
    assert legacy.code_hash is None and legacy.token_hash == ""


def test_lazy_expiry_clears_hash(admin, farm_id):
    invitation = _create(admin, farm_id)
    FarmInvitation.objects.filter(pk=invitation.pk).update(
        expires_at=timezone.now() - timedelta(minutes=1)
    )
    invitation_services.list_invitations(user=admin, farm_id=farm_id)
    invitation.refresh_from_db()
    assert invitation.status == InvitationStatus.EXPIRED
    assert invitation.code_hash is None
    assert invitation.token_hash == ""
    assert _events(farm_id, EventType.INVITATION_EXPIRED) == 1


def test_expire_command_clears_hash(admin, farm_id, make_user):
    second_admin = make_user("81000005")
    other_farm = services.create_farm(user=second_admin, name="Otra").farm_id
    first = _create(admin, farm_id)
    second = _create(second_admin, other_farm, email="otro@example.com")
    alive = _create(admin, farm_id, email="vigente@example.com")
    FarmInvitation.objects.filter(pk__in=[first.pk, second.pk]).update(
        expires_at=timezone.now() - timedelta(minutes=1)
    )

    out = StringIO()
    call_command("expire_farm_invitations", stdout=out)
    assert "2 invitaciones expiradas" in out.getvalue()

    for invitation in (first, second):
        invitation.refresh_from_db()
        assert invitation.status == InvitationStatus.EXPIRED
        assert invitation.code_hash is None
        assert invitation.token_hash == ""
    alive.refresh_from_db()
    assert alive.status == InvitationStatus.PENDING and alive.code_hash
    assert _no_inactive_hashes()


# --- Colisiones -----------------------------------------------------------------

def _pair(email, code):
    return code, invitation_codes.code_hash(email, code)


@pytest.mark.django_db(transaction=True)
def test_create_retries_once_on_code_hash_collision(admin, farm_id, monkeypatch):
    first = _pair(GUEST, "AAAAA-AAAAA")
    second = _pair(GUEST, "BBBBB-BBBBB")
    _occupy(first[1])
    calls = _sequence(monkeypatch, [first, second])

    invitation = _create(admin, farm_id)

    invitation.refresh_from_db()
    assert calls == [GUEST, GUEST]
    assert invitation.code_hash == second[1]
    assert FarmInvitation.objects.filter(farm_id=farm_id).count() == 1
    assert _events(farm_id, EventType.INVITATION_CREATED) == 1


@pytest.mark.django_db(transaction=True)
def test_three_collisions_raise_controlled_error_and_roll_back(admin, farm_id, monkeypatch):
    taken = _pair(GUEST, "CCCCC-CCCCC")
    _occupy(taken[1])
    stale = FarmInvitation.objects.create(
        farm_id=farm_id,
        email="vencida@example.com",
        pending_email="vencida@example.com",
        role=FarmRole.OPERADOR,
        token_hash="",
        expires_at=timezone.now() - timedelta(minutes=1),
    )
    calls = _sequence(monkeypatch, [taken, taken, taken])

    with pytest.raises(AppError) as info:
        _create(admin, farm_id)

    error = info.value
    assert calls == [GUEST] * invitation_services.MAX_CODE_ATTEMPTS == [GUEST] * 3
    assert error.status_code == 500
    assert error.message == invitation_services.INVITATION_CODE_UNAVAILABLE
    rendered = f"{error} {error!r} {error.extra} {error.__cause__!r} {error.__context__!r}"
    assert taken[1] not in rendered
    assert "Duplicate" not in rendered
    assert not FarmInvitation.objects.filter(farm_id=farm_id, email=GUEST).exists()
    assert _events(farm_id, EventType.INVITATION_CREATED) == 0
    # La expiración perezosa de cada intento también se revirtió.
    stale.refresh_from_db()
    assert stale.status == InvitationStatus.PENDING
    assert _events(farm_id, EventType.INVITATION_EXPIRED) == 0


@pytest.mark.django_db(transaction=True)
def test_resend_retries_on_collision_without_duplicate_events(admin, farm_id, monkeypatch):
    invitation = _create(admin, farm_id)
    taken = _pair(GUEST, "DDDDD-DDDDD")
    fresh = _pair(GUEST, "EEEEE-EEEEE")
    _occupy(taken[1])
    calls = _sequence(monkeypatch, [taken, fresh])

    invitation_services.resend_invitation(
        user=admin, farm_id=farm_id, invitation_id=invitation.pk
    )

    invitation.refresh_from_db()
    assert len(calls) == 2
    assert invitation.code_hash == fresh[1]
    assert _events(farm_id, EventType.INVITATION_RESEND_STARTED) == 1
    assert _events(farm_id, EventType.INVITATION_RESENT) == 1


@pytest.mark.django_db(transaction=True)
def test_change_role_retries_on_collision(admin, farm_id, monkeypatch):
    invitation = _create(admin, farm_id)
    taken = _pair(GUEST, "FFFFF-FFFFF")
    fresh = _pair(GUEST, "GGGGG-GGGGG")
    _occupy(taken[1])
    _sequence(monkeypatch, [taken, fresh])

    invitation_services.change_invitation_role(
        user=admin, farm_id=farm_id, invitation_id=invitation.pk, role=FarmRole.CONSULTA
    )

    invitation.refresh_from_db()
    assert invitation.code_hash == fresh[1]
    assert invitation.role == FarmRole.CONSULTA
    assert _events(farm_id, EventType.INVITATION_ROLE_CHANGED) == 1


def test_pending_email_conflict_keeps_business_response(admin, farm_id, issued):
    _create(admin, farm_id)
    with pytest.raises(AppError) as info:
        _create(admin, farm_id)
    assert info.value.status_code == 409
    assert info.value.message == "ya existe una invitación vigente para este correo"
    assert FarmInvitation.objects.filter(farm_id=farm_id).count() == 1
    assert _events(farm_id, EventType.INVITATION_CREATED) == 1


@pytest.mark.django_db(transaction=True)
def test_real_mariadb_errors_are_classified_exactly():
    """Comprueba el formato real de MariaDB para las dos claves únicas."""
    farm = Farm.objects.create(name="Finca errores")
    digest = "e" * 64
    _occupy(digest)
    with pytest.raises(IntegrityError) as code_error, transaction.atomic():
        FarmInvitation.objects.create(
            farm=farm, email="b@example.com", role=FarmRole.OPERADOR,
            code_hash=digest, expires_at=timezone.now(),
        )
    FarmInvitation.objects.create(
        farm=farm, email="c@example.com", pending_email="c@example.com",
        role=FarmRole.OPERADOR, expires_at=timezone.now(),
    )
    with pytest.raises(IntegrityError) as email_error, transaction.atomic():
        FarmInvitation.objects.create(
            farm=farm, email="c@example.com", pending_email="c@example.com",
            role=FarmRole.OPERADOR, expires_at=timezone.now(),
        )
    assert code_error.value.args[0] == 1062
    assert invitation_services._is_code_hash_collision(code_error.value) is True
    assert email_error.value.args[0] == 1062
    assert "uniq_farm_invitation_pending_email" in str(email_error.value.args[1])
    assert invitation_services._is_code_hash_collision(email_error.value) is False


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ((1062, "Duplicate entry 'x' for key 'code_hash'"), True),
        ((1062, "Duplicate entry 'x' for key 'farm_invitations.code_hash'"), True),
        ((1062, "Duplicate entry 'x' for key 'code_hash_2'"), False),
        ((1062, "Duplicate entry 'x' for key 'uniq_farm_invitation_pending_email'"), False),
        ((1452, "Cannot add or update a child row: for key 'code_hash'"), False),
        (("Duplicate entry for key 'code_hash'",), False),
        ((), False),
    ],
)
def test_collision_classifier_is_strict(args, expected):
    assert invitation_services._is_code_hash_collision(IntegrityError(*args)) is expected


@pytest.mark.django_db(transaction=True)
def test_other_integrity_errors_are_not_retried():
    farm = Farm.objects.create(name="Finca sin reintento")
    FarmInvitation.objects.create(
        farm=farm, email="d@example.com", pending_email="d@example.com",
        role=FarmRole.OPERADOR, expires_at=timezone.now(),
    )
    attempts = []

    def operation():
        attempts.append(1)
        FarmInvitation.objects.create(
            farm=farm, email="d@example.com", pending_email="d@example.com",
            role=FarmRole.OPERADOR, expires_at=timezone.now(),
        )

    with pytest.raises(IntegrityError):
        invitation_services._with_code_retry(operation)
    assert attempts == [1]


# --- Configuración ----------------------------------------------------------------

VALID_KEY = "k" * 48
OTHER_SECRET = "s" * 50


@pytest.mark.parametrize("raw", [None, "", "   ", "\t\n"])
def test_production_without_key_fails_closed(raw):
    with pytest.raises(ImproperlyConfigured) as info:
        resolve_invitation_code_key(raw, debug=False, secret_key=OTHER_SECRET)
    assert "INVITATION_CODE_HMAC_KEY" in str(info.value)
    assert "producción" in str(info.value)


def test_production_rejects_short_key_and_secret_key_reuse_without_revealing():
    short = "corta-" + secrets.token_hex(4)
    with pytest.raises(ImproperlyConfigured) as short_info:
        resolve_invitation_code_key(short, debug=False, secret_key=OTHER_SECRET)
    assert short not in str(short_info.value)
    shared = secrets.token_urlsafe(48)
    with pytest.raises(ImproperlyConfigured, match="independiente de SECRET_KEY") as info:
        resolve_invitation_code_key(shared, debug=False, secret_key=shared)
    assert shared not in str(info.value)


def test_production_accepts_valid_key():
    assert resolve_invitation_code_key(VALID_KEY, debug=False, secret_key=OTHER_SECRET) == VALID_KEY


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_development_does_not_require_key_nor_fall_back(raw):
    value = resolve_invitation_code_key(raw, debug=True, secret_key=OTHER_SECRET)
    assert value == ("" if raw is None else raw)
    assert value != OTHER_SECRET
    with pytest.raises(ImproperlyConfigured):
        check_invitation_code_key(value, secret_key=OTHER_SECRET)


def test_settings_wire_the_startup_validation():
    source = Path(django_settings.BASE_DIR, "config", "settings.py").read_text(encoding="utf-8")
    assert "resolve_invitation_code_key(" in source
    assert "platform.INVITATION_CODE_HMAC_KEY, debug=DEBUG, secret_key=SECRET_KEY" in source


def test_test_settings_hold_only_the_fictitious_key(settings):
    assert len(settings.INVITATION_CODE_HMAC_KEY) >= 32
    assert settings.INVITATION_CODE_HMAC_KEY != settings.SECRET_KEY


# --- Auditoría, logs y secretos ---------------------------------------------------------

def test_codes_and_hashes_never_reach_audit_or_logs(
    admin, farm_id, make_user, issued, fixed_token, caplog
):
    caplog.set_level(logging.DEBUG)
    guest = make_user("81000006", correo=GUEST)
    invitation = _create(admin, farm_id)
    invitation_services.resend_invitation(
        user=admin, farm_id=farm_id, invitation_id=invitation.pk
    )
    invitation_services.change_invitation_role(
        user=admin, farm_id=farm_id, invitation_id=invitation.pk, role=FarmRole.CONSULTA
    )
    invitation_services.accept_invitation(user=guest, token=fixed_token)
    other = _create(admin, farm_id, email="otra@example.com")
    invitation_services.cancel_invitation(
        user=admin, farm_id=farm_id, invitation_id=other.pk
    )

    secrets_seen = _secret_forms(issued)
    assert len(issued) == 4
    events = AuditEvent.objects.filter(farm_id=farm_id)
    dumped = json.dumps(
        [
            [e.before_data, e.after_data, e.metadata, e.reason, e.entity_id, e.actor_display]
            for e in events
        ],
        ensure_ascii=False,
    )
    for secret in secrets_seen:
        assert secret not in dumped
        assert secret not in caplog.text
    assert "code" not in dumped.lower()


@pytest.mark.parametrize("key", ["code_hash", "invitation_code", "invitationCode", "CODE-HASH"])
def test_audit_rejects_invitation_code_keys(admin, farm_id, key):
    with pytest.raises(ValidationError, match="clave sensible"):
        record_audit_event(
            farm=Farm.objects.get(pk=farm_id),
            actor=admin,
            event_type=EventType.INVITATION_CREATED,
            entity_type="FarmInvitation",
            entity_id="1",
            metadata={key: "x"},
        )


@pytest.mark.parametrize("key", ["code", "error_code", "postal_code", "event_code", "barcode"])
def test_audit_still_accepts_unrelated_code_keys(admin, farm_id, key):
    event = record_audit_event(
        farm=Farm.objects.get(pk=farm_id),
        actor=admin,
        event_type=EventType.INVITATION_CREATED,
        entity_type="FarmInvitation",
        entity_id="1",
        metadata={key: "valor"},
    )
    assert event.metadata == {key: "valor"}
