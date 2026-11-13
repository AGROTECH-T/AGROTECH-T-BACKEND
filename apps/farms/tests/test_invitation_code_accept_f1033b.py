# ruff: noqa: S101, S105, S106
"""Pruebas de F10.3.3-B: previsualizar y aceptar invitaciones por código.

Propósito: comprobar ``POST /api/v1/invitations/code/preview/`` y
``/code/accept/``: la identidad sale de la cuenta autenticada, las respuestas
son genéricas, la membresía y el consumo del código son atómicos, la
concurrencia está controlada, los intentos fallidos se limitan por cuenta y
por IP, y ni el código ni su huella aparecen en respuestas, auditoría, logs ni
informes de error.
Contexto: solo en la base desechable del orquestador. El correo se sustituye
por un buzón en memoria y la clave HMAC es ficticia (``conftest.token_key``).
La caché de pruebas es ``LocMemCache``: no se ejerce Redis.
"""
import json
import logging
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.conf import settings as django_settings
from django.core.cache import cache
from django.core.cache.backends import base as cache_base
from django.core.cache.backends import locmem
from django.db import connection, connections
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from django.views.debug import ExceptionReporter
from rest_framework.exceptions import Throttled
from rest_framework.test import APIClient

from apps.accounts.errors import AppError
from apps.accounts.integrations.delivery import DeliveryOutcome
from apps.farms import code_attempts, invitation_codes, invitation_services, services
from apps.farms.models import (
    FarmInvitation,
    FarmMembership,
    FarmRole,
    InvitationStatus,
    MembershipStatus,
)
from apps.trazabilidad.models import AuditEvent, EventType

pytestmark = pytest.mark.django_db

GUEST = "invitada@example.com"
PREVIEW = "/api/v1/invitations/code/preview/"
ACCEPT = "/api/v1/invitations/code/accept/"
_GROUP = "[ABCDEFGHJKMNPQRSTUVWXYZ23456789]{5}"
CODE_RE = re.compile(rf"\b{_GROUP}-{_GROUP}\b")
GENERIC_404 = {"error": invitation_services.INVITATION_NOT_FOUND}
WRONG_CODE = "ZZZZZ-ZZZZZ"
_ISSUED: list[str] = []


@pytest.fixture(autouse=True)
def mailbox(monkeypatch):
    sent = []

    def deliver(*, to, subject, text, html, timeout=15):
        found = CODE_RE.findall(text)
        sent.append({"to": to, "code": found[0] if found else None})
        return DeliveryOutcome.ACCEPTED

    monkeypatch.setattr(invitation_services, "deliver_email", deliver)
    return sent


@pytest.fixture
def admin(make_user):
    return make_user("84000001", correo="admin-f1033b@example.com")


@pytest.fixture
def farm_id(admin):
    return services.create_farm(user=admin, name="El Roble").farm_id


@pytest.fixture
def guest(make_user):
    return make_user("84000002", correo=GUEST)


def _invite(admin, farm_id, mailbox, email=GUEST, role=FarmRole.OPERADOR):
    invitation = invitation_services.create_invitation(
        user=admin, farm_id=farm_id, email=email, role=role
    )
    return invitation, mailbox[-1]["code"]


def _client(user, ip="10.0.0.1"):
    client = APIClient(REMOTE_ADDR=ip)
    client.force_authenticate(user=user)
    return client


def _post(client, url, code, **extra):
    return client.post(url, {"code": code}, format="json", **extra)


def _events(farm_id, event_type):
    return AuditEvent.objects.filter(farm_id=farm_id, event_type=event_type)


def _secrets(code, email=GUEST):
    return {code, code.replace("-", ""), invitation_codes.code_hash(email, code)}


# --- Previsualización ------------------------------------------------------------------

def test_preview_valid_code_returns_only_confirmation_data(admin, farm_id, guest, mailbox):
    invitation, code = _invite(admin, farm_id, mailbox, role=FarmRole.CONSULTA)
    response = _post(_client(guest), PREVIEW, code)
    assert response.status_code == 200
    assert set(response.data) == {"farm_name", "role", "expires_at"}
    assert response.data["farm_name"] == "El Roble"
    assert response.data["role"] == FarmRole.CONSULTA
    body = response.content.decode()
    for secret in (*_secrets(code), invitation.token_hash, admin.correo, admin.identification):
        assert secret not in body


def test_preview_accepts_presentation_variants(admin, farm_id, guest, mailbox):
    _invitation, code = _invite(admin, farm_id, mailbox)
    for variant in (code.lower(), code.replace("-", ""), f"  {code}  "):
        assert _post(_client(guest), PREVIEW, variant).status_code == 200


def test_preview_is_read_only(admin, farm_id, guest, mailbox):
    invitation, code = _invite(admin, farm_id, mailbox)
    before = FarmInvitation.objects.get(pk=invitation.pk)
    events_before = AuditEvent.objects.count()
    with CaptureQueriesContext(connection) as queries:
        response = _post(_client(guest), PREVIEW, code)
    assert response.status_code == 200
    writes = [
        q["sql"] for q in queries.captured_queries
        if not q["sql"].lstrip().upper().startswith("SELECT")
    ]
    assert writes == []
    after = FarmInvitation.objects.get(pk=invitation.pk)
    assert (after.status, after.code_hash, after.updated_at) == (
        before.status, before.code_hash, before.updated_at
    )
    assert not FarmMembership.objects.filter(farm_id=farm_id, user=guest).exists()
    assert AuditEvent.objects.count() == events_before
    assert code_attempts.failed_attempts(guest.pk) == 0


@pytest.mark.parametrize(
    "case", ["wrong", "expired", "cancelled", "accepted", "other_email", "inactive_farm"]
)
def test_preview_rejects_with_generic_404(admin, farm_id, guest, make_user, mailbox, case):
    invitation, code = _invite(admin, farm_id, mailbox)
    client = _client(guest)
    if case == "wrong":
        code = WRONG_CODE
    elif case == "expired":
        FarmInvitation.objects.filter(pk=invitation.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
    elif case == "cancelled":
        invitation_services.cancel_invitation(
            user=admin, farm_id=farm_id, invitation_id=invitation.pk
        )
    elif case == "accepted":
        assert _post(client, ACCEPT, code).status_code == 200
    elif case == "other_email":
        client = _client(make_user("84000003", correo="otra@example.com"))
    elif case == "inactive_farm":
        services.deactivate_farm(user=admin, farm_id=farm_id)
    response = _post(client, PREVIEW, code)
    assert response.status_code == 404
    assert response.data == GENERIC_404


@pytest.mark.parametrize("raw", ["ABCDE-FGHJ0", "ABC", "ABCDE-FGHJſ", "   "])
def test_preview_malformed_code_is_generic_404_and_counted(guest, raw):
    response = _post(_client(guest), PREVIEW, raw)
    assert response.status_code == 404
    assert response.data == GENERIC_404
    assert code_attempts.failed_attempts(guest.pk) == 1


def test_missing_or_empty_code_is_a_contract_error(guest):
    assert _post(_client(guest), PREVIEW, "").status_code == 400
    assert _client(guest).post(ACCEPT, {}, format="json").status_code == 400
    assert code_attempts.failed_attempts(guest.pk) == 0


def test_preview_unverified_email_is_403_and_not_counted(admin, farm_id, make_user, mailbox):
    _invitation, code = _invite(admin, farm_id, mailbox)
    unverified = make_user("84000004", correo=GUEST, email_verified=False)
    response = _post(_client(unverified), PREVIEW, code)
    assert response.status_code == 403
    assert response.data == {"error": invitation_services.EMAIL_NOT_VERIFIED}
    assert code_attempts.failed_attempts(unverified.pk) == 0


def test_preview_and_accept_inactive_account_follow_auth_policy(
    admin, farm_id, guest, login, mailbox
):
    _invitation, code = _invite(admin, farm_id, mailbox)
    client = login(guest)
    guest.is_active = False
    guest.save(update_fields=["is_active"])
    assert _post(client, PREVIEW, code).status_code == 401
    assert _post(client, ACCEPT, code).status_code == 401
    assert not FarmMembership.objects.filter(user=guest).exists()


def test_service_rejects_inactive_account_with_403(admin, farm_id, guest, mailbox):
    _invitation, code = _invite(admin, farm_id, mailbox)
    guest.is_active = False
    for operation in (
        invitation_services.preview_invitation_code,
        invitation_services.accept_invitation_code,
    ):
        with pytest.raises(AppError) as info:
            operation(user=guest, code=code)
        assert info.value.status_code == 403


def test_unauthenticated_requests_are_401(admin, farm_id, mailbox):
    _invitation, code = _invite(admin, farm_id, mailbox)
    assert APIClient().post(PREVIEW, {"code": code}, format="json").status_code == 401
    assert APIClient().post(ACCEPT, {"code": code}, format="json").status_code == 401


def test_body_cannot_carry_identity(admin, farm_id, guest, mailbox):
    _invitation, code = _invite(admin, farm_id, mailbox)
    response = _client(guest).post(
        PREVIEW, {"code": code, "email": "otra@example.com"}, format="json"
    )
    assert response.status_code == 400
    response = _client(guest).post(ACCEPT, {"email": GUEST}, format="json")
    assert response.status_code == 400


def test_preview_already_member_is_409(admin, farm_id, guest, mailbox):
    _invitation, code = _invite(admin, farm_id, mailbox)
    FarmMembership.objects.create(farm_id=farm_id, user=guest, role=FarmRole.CONSULTA)
    response = _post(_client(guest), PREVIEW, code)
    assert response.status_code == 409
    assert code_attempts.failed_attempts(guest.pk) == 0


# --- Aceptación ---------------------------------------------------------------------

def test_accept_creates_membership_with_offered_role_and_consumes_code(
    admin, farm_id, guest, mailbox
):
    invitation, code = _invite(admin, farm_id, mailbox, role=FarmRole.CONSULTA)
    client = _client(guest)
    assert _post(client, PREVIEW, code).status_code == 200

    response = _post(client, ACCEPT, code)

    assert response.status_code == 200
    membership = FarmMembership.objects.get(farm_id=farm_id, user=guest)
    assert response.data == {
        "farm_id": farm_id,
        "membership_id": membership.pk,
        "role": FarmRole.CONSULTA,
        "status": MembershipStatus.ACTIVE,
    }
    assert membership.role == FarmRole.CONSULTA
    invitation.refresh_from_db()
    assert invitation.status == InvitationStatus.ACCEPTED
    assert invitation.code_hash is None and invitation.token_hash == ""
    assert invitation.accepted_at is not None
    accepted = _events(farm_id, EventType.INVITATION_ACCEPTED).get()
    assert accepted.metadata == {"channel": "code"}
    assert accepted.actor_id == guest.pk
    assert _events(farm_id, EventType.MEMBER_JOINED).count() == 1


def test_accept_reuse_is_rejected(admin, farm_id, guest, mailbox):
    _invitation, code = _invite(admin, farm_id, mailbox)
    client = _client(guest)
    assert _post(client, ACCEPT, code).status_code == 200
    again = _post(client, ACCEPT, code)
    assert again.status_code == 404
    assert again.data == GENERIC_404
    assert FarmMembership.objects.filter(farm_id=farm_id, user=guest).count() == 1


def test_accept_already_member_is_409_without_duplicate(admin, farm_id, guest, mailbox):
    invitation, code = _invite(admin, farm_id, mailbox)
    FarmMembership.objects.create(farm_id=farm_id, user=guest, role=FarmRole.CONSULTA)
    response = _post(_client(guest), ACCEPT, code)
    assert response.status_code == 409
    assert response.data == {"error": invitation_services.ALREADY_MEMBER}
    assert FarmMembership.objects.filter(farm_id=farm_id, user=guest).count() == 1
    invitation.refresh_from_db()
    assert invitation.status == InvitationStatus.PENDING


@pytest.mark.parametrize("case", ["other_email", "cancelled", "expired", "role_changed"])
def test_accept_rejections_are_generic(admin, farm_id, guest, make_user, mailbox, case):
    invitation, code = _invite(admin, farm_id, mailbox)
    client = _client(guest)
    if case == "other_email":
        client = _client(make_user("84000005", correo="otra@example.com"))
    elif case == "cancelled":
        invitation_services.cancel_invitation(
            user=admin, farm_id=farm_id, invitation_id=invitation.pk
        )
    elif case == "expired":
        FarmInvitation.objects.filter(pk=invitation.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
    elif case == "role_changed":
        invitation_services.change_invitation_role(
            user=admin, farm_id=farm_id, invitation_id=invitation.pk, role=FarmRole.CONSULTA
        )
    response = _post(client, ACCEPT, code)
    assert response.status_code == 404
    assert response.data == GENERIC_404
    assert not FarmMembership.objects.filter(farm_id=farm_id).exclude(user=admin).exists()


def test_accept_unverified_email_is_403(admin, farm_id, make_user, mailbox):
    _invitation, code = _invite(admin, farm_id, mailbox)
    unverified = make_user("84000006", correo=GUEST, email_verified=False)
    response = _post(_client(unverified), ACCEPT, code)
    assert response.status_code == 403
    assert not FarmMembership.objects.filter(user=unverified).exists()


def test_accept_reactivates_membership_and_clears_permissions(
    admin, farm_id, guest, mailbox, monkeypatch
):
    previous = FarmMembership.objects.create(
        farm_id=farm_id, user=guest, role=FarmRole.ADMINISTRADOR,
        status=MembershipStatus.REVOKED, revoked_at=timezone.now(), revoked_by=admin,
    )
    _invitation, code = _invite(admin, farm_id, mailbox, role=FarmRole.OPERADOR)
    cleared = []
    real = invitation_services.farm_services._clear_membership_permissions

    def spy(**kwargs):
        cleared.append(kwargs["cause"])
        return real(**kwargs)

    monkeypatch.setattr(invitation_services.farm_services, "_clear_membership_permissions", spy)
    response = _post(_client(guest), ACCEPT, code)
    assert response.status_code == 200
    previous.refresh_from_db()
    assert response.data["membership_id"] == previous.pk
    assert previous.status == MembershipStatus.ACTIVE
    assert previous.role == FarmRole.OPERADOR  # solo el rol de la invitación
    assert previous.revoked_at is None and previous.revoked_by is None
    assert cleared == ["member_reactivated"]
    assert _events(farm_id, EventType.MEMBER_REACTIVATED).count() == 1


@pytest.mark.parametrize("failing", ["membership", "audit"])
def test_accept_rolls_back_everything_on_failure(
    admin, farm_id, guest, mailbox, monkeypatch, failing
):
    invitation, code = _invite(admin, farm_id, mailbox)
    accepted_before = _events(farm_id, EventType.INVITATION_ACCEPTED).count()
    if failing == "membership":
        def boom(**kwargs):
            raise RuntimeError("fallo simulado al crear la membresía")

        monkeypatch.setattr(FarmMembership.objects, "create", boom)
    else:
        real = invitation_services.record_audit_event

        def fail_on_join(**kwargs):
            if kwargs["event_type"] == EventType.MEMBER_JOINED:
                raise RuntimeError("fallo simulado de auditoría")
            return real(**kwargs)

        monkeypatch.setattr(invitation_services, "record_audit_event", fail_on_join)

    with pytest.raises(RuntimeError):
        invitation_services.accept_invitation_code(user=guest, code=code)

    invitation.refresh_from_db()
    assert invitation.status == InvitationStatus.PENDING
    assert invitation.code_hash == invitation_codes.code_hash(GUEST, code)
    assert not FarmMembership.objects.filter(farm_id=farm_id, user=guest).exists()
    assert _events(farm_id, EventType.INVITATION_ACCEPTED).count() == accepted_before
    monkeypatch.undo()
    retried = invitation_services.accept_invitation_code(user=guest, code=code)
    assert retried.role == FarmRole.OPERADOR


# --- Enlaces antiguos -----------------------------------------------------------------

def test_legacy_link_and_new_code_coexist(admin, farm_id, guest, make_user, mailbox):
    legacy = FarmInvitation.objects.create(
        farm_id=farm_id, email=GUEST, pending_email=GUEST, role=FarmRole.CONSULTA,
        token_hash=invitation_services._hash_token("enlace-f1033b"),
        expires_at=timezone.now() + timedelta(days=1),
    )
    assert legacy.code_hash is None
    membership = invitation_services.accept_invitation(user=guest, token="enlace-f1033b")
    legacy.refresh_from_db()
    assert membership.role == FarmRole.CONSULTA
    assert legacy.status == InvitationStatus.ACCEPTED
    assert legacy.token_hash == "" and legacy.code_hash is None
    accepted = _events(farm_id, EventType.INVITATION_ACCEPTED).get()
    assert accepted.metadata == {}  # el flujo por enlace no cambia su evento

    other = make_user("84000007", correo="nuevo@example.com")
    _invitation, code = _invite(admin, farm_id, mailbox, email="nuevo@example.com")
    assert _post(_client(other), ACCEPT, code).status_code == 200
    with pytest.raises(AppError) as reused:
        invitation_services.accept_invitation(user=guest, token="enlace-f1033b")
    assert reused.value.status_code == 404


def test_code_invitation_link_token_is_not_accepted_after_code_use(
    admin, farm_id, guest, mailbox, monkeypatch
):
    monkeypatch.setattr(invitation_services, "_new_token", lambda: "token-no-entregado")
    _invitation, code = _invite(admin, farm_id, mailbox)
    assert _post(_client(guest), ACCEPT, code).status_code == 200
    with pytest.raises(AppError) as info:
        invitation_services.accept_invitation(user=guest, token="token-no-entregado")
    assert info.value.status_code == 404
    assert FarmMembership.objects.filter(farm_id=farm_id, user=guest).count() == 1


# --- Límite de intentos ---------------------------------------------------------------

def test_five_failures_then_sixth_attempt_blocked_even_with_valid_code(
    admin, farm_id, guest, mailbox
):
    invitation, code = _invite(admin, farm_id, mailbox)
    client = _client(guest)
    for _ in range(code_attempts.MAX_FAILED_ATTEMPTS):
        assert _post(client, PREVIEW, WRONG_CODE).status_code == 404
    assert code_attempts.failed_attempts(guest.pk) == 5

    blocked = _post(client, ACCEPT, code)
    assert blocked.status_code == 429
    assert "Límite de intentos" in blocked.data["error"]
    invitation.refresh_from_db()
    assert invitation.status == InvitationStatus.PENDING  # no se consumió
    for secret in _secrets(code):
        assert secret not in blocked.content.decode()


def test_preview_and_accept_share_the_failure_counter(admin, farm_id, guest, mailbox):
    _invitation, code = _invite(admin, farm_id, mailbox)
    client = _client(guest)
    for url in (PREVIEW, ACCEPT, PREVIEW, ACCEPT, "ABCDE-FGHJ0"):
        target, sent = (PREVIEW, url) if url not in (PREVIEW, ACCEPT) else (url, WRONG_CODE)
        assert _post(client, target, sent).status_code == 404
    assert _post(client, PREVIEW, code).status_code == 429


def test_successful_attempts_do_not_count(admin, farm_id, guest, mailbox):
    _invitation, code = _invite(admin, farm_id, mailbox)
    client = _client(guest)
    for _ in range(4):
        assert _post(client, PREVIEW, WRONG_CODE).status_code == 404
    for _ in range(3):
        assert _post(client, PREVIEW, code).status_code == 200
    assert code_attempts.failed_attempts(guest.pk) == 4
    assert _post(client, ACCEPT, code).status_code == 200


def test_counter_is_per_account(admin, farm_id, guest, make_user, mailbox):
    other = make_user("84000008", correo="otra@example.com")
    for _ in range(5):
        _post(_client(guest), PREVIEW, WRONG_CODE)
    _invitation, code = _invite(admin, farm_id, mailbox, email="otra@example.com")
    assert _post(_client(other, ip="10.0.0.2"), PREVIEW, code).status_code == 200


def _redis_window_client():
    """Cliente redis-py si la caché es el Redis desechable (-WithRedis); si no, None."""
    from apps.farms.atomic_cache import redis_client

    return redis_client()


def test_window_is_fifteen_minutes(guest, monkeypatch):
    """Ventana de 15 minutos con LocMemCache (reloj simulado) o Redis (TTL real).

    Cierre F10.3: con Redis el reloj de LocMemCache no interviene; se comprueba
    el TTL real de la ventana y se adelanta su vencimiento con PEXPIRE.
    """
    client = _client(guest)
    redis = _redis_window_client()
    if redis is None:
        now = [time.time()]
        fake = SimpleNamespace(time=lambda: now[0])
        monkeypatch.setattr(code_attempts, "_clock", fake.time)
        monkeypatch.setattr(locmem, "time", fake)  # caducidad de LocMemCache
        monkeypatch.setattr(cache_base, "time", fake)  # cálculo del TTL
    for _ in range(5):
        assert _post(client, PREVIEW, WRONG_CODE).status_code == 404
    if redis is None:
        now[0] += 14 * 60
        assert _post(client, PREVIEW, WRONG_CODE).status_code == 429
        now[0] += 61
    else:
        from apps.farms.atomic_cache import raw_key

        window_key = raw_key(f"farms:invitation-code-attempts:{guest.pk}:window")
        assert 15 * 60 - 10 <= redis.ttl(window_key) <= 15 * 60
        assert _post(client, PREVIEW, WRONG_CODE).status_code == 429
        redis.pexpire(window_key, 1)  # vencimiento real de la ventana en Redis
        time.sleep(0.05)
    assert _post(client, PREVIEW, WRONG_CODE).status_code == 404
    assert code_attempts.failed_attempts(guest.pk) == 1


def test_concurrent_burst_cannot_exceed_the_limit(guest):
    attempts = code_attempts.MAX_FAILED_ATTEMPTS + 7
    barrier = threading.Barrier(attempts)

    def attempt():
        barrier.wait(timeout=10)
        guard = code_attempts.CodeAttemptGuard(guest.pk)
        try:
            guard.begin()
            return "allowed"
        except Exception:  # noqa: BLE001 - Throttled
            return "blocked"

    with ThreadPoolExecutor(max_workers=attempts) as pool:
        results = list(pool.map(lambda _i: attempt(), range(attempts)))
    assert results.count("allowed") == code_attempts.MAX_FAILED_ATTEMPTS


def test_counter_lives_in_the_shared_cache_backend(guest):
    """Guardas independientes comparten el estado guardado en la caché ``default``.

    Cierre F10.3: el estado es una ventana por cuenta (LocMemCache o Redis
    según la configuración). La compartición entre procesos reales con Redis la
    demuestra ``test_code_attempts_redis_f1034b.py``.
    """
    from apps.farms.atomic_cache import raw_key, redis_client

    for _ in range(code_attempts.MAX_FAILED_ATTEMPTS):
        guard = code_attempts.CodeAttemptGuard(guest.pk)
        guard.begin()
        guard.failed()
    with pytest.raises(Throttled):
        code_attempts.CodeAttemptGuard(guest.pk).begin()
    name = f"farms:invitation-code-attempts:{guest.pk}:window"
    redis = redis_client()
    if redis is None:
        stored = cache.get(name)
        assert (stored["f"], stored["p"]) == (code_attempts.MAX_FAILED_ATTEMPTS, 0)
    else:
        assert redis.hmget(raw_key(name), ["f", "p"]) == [b"5", b"0"]


def test_production_cache_is_shared_redis():
    source = Path(django_settings.BASE_DIR, "config", "platform.py").read_text(encoding="utf-8")
    assert 'raise ImproperlyConfigured("REDIS_URL es obligatoria en producción")' in source
    assert '"django_redis.cache.RedisCache"' in source


def test_ip_limit_applies_across_accounts_and_ignores_forwarded_for(make_user):
    rate = django_settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["invitation_accept_ip"]
    limit = int(rate.split("/")[0])
    assert rate == "30/hour"
    users = [
        make_user(f"8500{index:04d}", correo=f"ip{index}@example.com")
        for index in range(limit + 1)
    ]
    statuses = []
    for index, user in enumerate(users):
        response = _post(
            _client(user, ip="203.0.113.7"),
            PREVIEW if index % 2 else ACCEPT,
            WRONG_CODE,
            HTTP_X_FORWARDED_FOR=f"198.51.100.{index}",
        )
        statuses.append(response.status_code)
    assert statuses[:limit] == [404] * limit
    assert statuses[limit] == 429
    fresh_ip = _post(_client(users[-1], ip="203.0.113.8"), PREVIEW, WRONG_CODE)
    assert fresh_ip.status_code == 404


def test_code_endpoints_share_account_quota_with_link_acceptance():
    from apps.farms.throttles import InvitationAcceptIpThrottle
    from apps.farms.views import (
        FarmInvitationAcceptView,
        FarmInvitationCodeAcceptView,
        FarmInvitationCodePreviewView,
    )

    for view in (FarmInvitationCodePreviewView, FarmInvitationCodeAcceptView):
        assert view.throttle_scope == FarmInvitationAcceptView.throttle_scope == "invitation_accept"
        assert InvitationAcceptIpThrottle in view.throttle_classes


# --- Seguridad ---------------------------------------------------------------------------

def test_code_and_hash_absent_from_responses_audit_logs_and_sql(
    admin, farm_id, guest, mailbox, caplog
):
    caplog.set_level(logging.DEBUG)
    _invitation, code = _invite(admin, farm_id, mailbox)
    client = _client(guest)
    with CaptureQueriesContext(connection) as queries:
        responses = [
            _post(client, PREVIEW, WRONG_CODE),
            _post(client, PREVIEW, code),
            _post(client, ACCEPT, code),
            _post(client, ACCEPT, code),
        ]
    assert [r.status_code for r in responses] == [404, 200, 200, 404]
    plain = {code, code.replace("-", ""), WRONG_CODE, WRONG_CODE.replace("-", "")}
    digest = invitation_codes.code_hash(GUEST, code)
    events = json.dumps(
        [
            [e.before_data, e.after_data, e.metadata, e.reason, e.entity_id]
            for e in AuditEvent.objects.filter(farm_id=farm_id)
        ]
    )
    sql = "\n".join(q["sql"] for q in queries.captured_queries).upper()
    for secret in (*plain, digest):
        for response in responses:
            assert secret not in response.content.decode()
        assert secret not in events
        assert secret not in caplog.text
    for secret in plain:
        assert secret not in sql  # solo viaja la huella


def test_error_reports_hide_code_and_hash(admin, farm_id, guest, mailbox, monkeypatch):
    _ISSUED.clear()
    _invitation, code = _invite(admin, farm_id, mailbox)
    _ISSUED.append(code)
    del code

    def boom(**kwargs):
        raise RuntimeError("fallo simulado")

    monkeypatch.setattr(FarmMembership.objects, "create", boom)
    client = _client(guest)
    client.raise_request_exception = True
    with pytest.raises(RuntimeError):
        try:
            _post(client, ACCEPT, _ISSUED[0])
        except RuntimeError:
            reporter = ExceptionReporter(None, *sys.exc_info())
            text = reporter.get_traceback_text()
            frames = reporter.get_traceback_data()["frames"]
            raise
    issued = _ISSUED.pop()
    # Se excluyen los marcos del arnés (esta prueba y el cliente de pruebas, que
    # tiene el cuerpo de la petición): en producción la traza empieza en Django.
    harness = ("test_invitation_code_accept", "/django/test/", "/rest_framework/test.py")

    def in_harness(frame) -> bool:
        path = str(frame["filename"]).replace(chr(92), "/")
        return any(marker in path for marker in harness)

    app_frames = [f for f in frames if not in_harness(f)]
    assert any("invitation_services" in f["filename"] for f in app_frames)
    variables = " ".join(str(value) for frame in app_frames for _name, value in frame["vars"])
    for secret in _secrets(issued):
        assert secret not in variables
    assert "fallo simulado" in text


# --- Concurrencia -----------------------------------------------------------------------

def _threaded(*calls):
    barrier = threading.Barrier(len(calls))

    def wrap(fn):
        def run():
            try:
                barrier.wait(timeout=10)
                return fn()
            except AppError as exc:
                return exc
            finally:
                connections.close_all()

        return run

    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        futures = [pool.submit(wrap(fn)) for fn in calls]
        return [future.result(timeout=30) for future in futures]


@pytest.mark.django_db(transaction=True)
def test_concurrent_accepts_create_a_single_membership(admin, farm_id, guest, mailbox):
    _invitation, code = _invite(admin, farm_id, mailbox)
    results = _threaded(
        lambda: invitation_services.accept_invitation_code(user=guest, code=code),
        lambda: invitation_services.accept_invitation_code(user=guest, code=code),
    )
    members = [r for r in results if isinstance(r, FarmMembership)]
    errors = [r for r in results if isinstance(r, AppError)]
    assert len(members) == 1 and len(errors) == 1
    assert errors[0].status_code in (404, 409)
    assert FarmMembership.objects.filter(farm_id=farm_id, user=guest).count() == 1
    assert _events(farm_id, EventType.INVITATION_ACCEPTED).count() == 1
    assert _events(farm_id, EventType.MEMBER_JOINED).count() == 1


@pytest.mark.django_db(transaction=True)
def test_two_tabs_accepting_over_http(admin, farm_id, guest, mailbox):
    _invitation, code = _invite(admin, farm_id, mailbox)
    tabs = [_client(guest), _client(guest)]
    results = _threaded(*(lambda tab=tab: _post(tab, ACCEPT, code) for tab in tabs))
    assert sorted(r.status_code for r in results) in ([200, 404], [200, 409])
    assert FarmMembership.objects.filter(farm_id=farm_id, user=guest).count() == 1


@pytest.mark.django_db(transaction=True)
def test_preview_racing_accept(admin, farm_id, guest, mailbox):
    _invitation, code = _invite(admin, farm_id, mailbox)
    preview, accepted = _threaded(
        lambda: invitation_services.preview_invitation_code(user=guest, code=code),
        lambda: invitation_services.accept_invitation_code(user=guest, code=code),
    )
    assert isinstance(accepted, FarmMembership)
    assert isinstance(preview, dict) or preview.status_code == 404
    assert FarmMembership.objects.filter(farm_id=farm_id, user=guest).count() == 1


@pytest.mark.django_db(transaction=True)
def test_cancel_racing_accept(admin, farm_id, guest, mailbox):
    invitation, code = _invite(admin, farm_id, mailbox)
    cancel, accept = _threaded(
        lambda: invitation_services.cancel_invitation(
            user=admin, farm_id=farm_id, invitation_id=invitation.pk
        ),
        lambda: invitation_services.accept_invitation_code(user=guest, code=code),
    )
    invitation.refresh_from_db()
    member = FarmMembership.objects.filter(farm_id=farm_id, user=guest)
    if isinstance(accept, FarmMembership):
        assert isinstance(cancel, AppError) and cancel.status_code == 409
        assert invitation.status == InvitationStatus.ACCEPTED and member.count() == 1
    else:
        assert accept.status_code == 404
        assert invitation.status == InvitationStatus.CANCELLED and not member.exists()
    assert invitation.code_hash is None


@pytest.mark.django_db(transaction=True)
def test_expiry_racing_accept(admin, farm_id, guest, mailbox):
    invitation, code = _invite(admin, farm_id, mailbox)
    later = timezone.now() + timedelta(days=8)
    expired, accept = _threaded(
        lambda: invitation_services.expire_pending_invitations(now=later),
        lambda: invitation_services.accept_invitation_code(user=guest, code=code),
    )
    invitation.refresh_from_db()
    member = FarmMembership.objects.filter(farm_id=farm_id, user=guest)
    if isinstance(accept, FarmMembership):
        assert expired == 0
        assert invitation.status == InvitationStatus.ACCEPTED and member.count() == 1
    else:
        assert expired == 1 and accept.status_code == 404
        assert invitation.status == InvitationStatus.EXPIRED and not member.exists()
    assert invitation.code_hash is None


@pytest.mark.django_db(transaction=True)
def test_role_change_racing_accept(admin, farm_id, guest, mailbox):
    invitation, code = _invite(admin, farm_id, mailbox, role=FarmRole.OPERADOR)
    change, accept = _threaded(
        lambda: invitation_services.change_invitation_role(
            user=admin, farm_id=farm_id, invitation_id=invitation.pk, role=FarmRole.CONSULTA
        ),
        lambda: invitation_services.accept_invitation_code(user=guest, code=code),
    )
    invitation.refresh_from_db()
    member = FarmMembership.objects.filter(farm_id=farm_id, user=guest)
    if isinstance(accept, FarmMembership):
        # Aceptó antes del cambio: rol original y el cambio llega tarde (409).
        assert isinstance(change, AppError) and change.status_code == 409
        assert member.get().role == FarmRole.OPERADOR
        assert invitation.status == InvitationStatus.ACCEPTED
    else:
        # El cambio ganó: el código anterior ya no vale y nadie entra con el rol previo.
        assert accept.status_code == 404 and not member.exists()
        assert invitation.role == FarmRole.CONSULTA
        assert invitation.code_hash == invitation_codes.code_hash(GUEST, mailbox[-1]["code"])
