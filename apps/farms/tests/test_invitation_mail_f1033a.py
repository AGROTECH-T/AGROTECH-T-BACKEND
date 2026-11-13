# ruff: noqa: S101, S105, S106
"""Pruebas de F10.3.3-A: entrega del código de invitación por correo.

Propósito: comprobar que crear, reenviar y cambiar el rol envían un correo con
el código (sin enlace), después de confirmar la transacción. También que el
código solo existe en memoria, que ``delivery_nonce`` impide que una entrega
antigua altere la vigente, y que el código no aparece en la base, la API, la
auditoría, los logs, el SQL ni los informes de error.
Contexto: solo en la base desechable del orquestador. ``deliver_email`` se
sustituye por un buzón en memoria; no se envía nada y nada se persiste.
"""
import json
import logging
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.db import connection, connections, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from django.views.debug import ExceptionReporter

from apps.accounts.errors import AppError
from apps.accounts.integrations.delivery import DeliveryOutcome
from apps.farms import invitation_codes, invitation_mail, invitation_services, services
from apps.farms.models import FarmInvitation, FarmRole, InvitationStatus
from apps.trazabilidad.models import AuditEvent, EventType

pytestmark = pytest.mark.django_db

GUEST = "invitada@example.com"
_GROUP = "[ABCDEFGHJKMNPQRSTUVWXYZ23456789]{5}"
CODE_RE = re.compile(rf"\b{_GROUP}-{_GROUP}\b")
FIXED_TOKEN = "token-que-no-debe-viajar-f1033a"
# Fuera de las variables locales de la prueba, para que el informe de error
# analizado no los muestre por estar en el marco de la propia prueba.
_ISSUED: list[str] = []


class Mailbox:
    """Buzón en memoria que sustituye a ``deliver_email``."""

    def __init__(self):
        self.messages: list[dict] = []
        self.outcome = DeliveryOutcome.ACCEPTED
        self.lock = threading.Lock()
        self.on_send = None

    def __call__(self, *, to, subject, text, html, timeout=15):
        message = {
            "to": to,
            "subject": subject,
            "text": text,
            "html": html,
            "in_atomic_block": transaction.get_connection().in_atomic_block,
            "thread": threading.current_thread().name,
        }
        with self.lock:
            self.messages.append(message)
        if self.on_send is not None:
            return self.on_send(message)
        return self.outcome

    def codes(self) -> list[str]:
        return [_code_in(message) for message in self.messages]


@pytest.fixture
def mailbox(monkeypatch):
    box = Mailbox()
    monkeypatch.setattr(invitation_services, "deliver_email", box)
    return box


@pytest.fixture
def fixed_token(monkeypatch):
    monkeypatch.setattr(invitation_services, "_new_token", lambda: FIXED_TOKEN)
    return FIXED_TOKEN


@pytest.fixture
def admin(make_user):
    return make_user("83000001", correo="admin-f1033a@example.com")


@pytest.fixture
def farm_id(admin):
    return services.create_farm(user=admin, name="La Esperanza").farm_id


def _code_in(message) -> str:
    found = set(CODE_RE.findall(message["text"]))
    assert len(found) == 1, "el correo debe llevar exactamente un código"
    code = found.pop()
    assert code in message["html"]
    return code


def _create(admin, farm_id, email=GUEST, role=FarmRole.OPERADOR):
    return invitation_services.create_invitation(
        user=admin, farm_id=farm_id, email=email, role=role
    )


def _resend(admin, farm_id, invitation_id):
    return invitation_services.resend_invitation(
        user=admin, farm_id=farm_id, invitation_id=invitation_id
    )


def _change_role(admin, farm_id, invitation_id, role):
    return invitation_services.change_invitation_role(
        user=admin, farm_id=farm_id, invitation_id=invitation_id, role=role
    )


def _events(farm_id, event_type):
    return AuditEvent.objects.filter(farm_id=farm_id, event_type=event_type).count()


def _hash(code, email=GUEST):
    return invitation_codes.code_hash(email, code)


def _forms(codes) -> set:
    return {form for code in codes for form in (code, code.replace("-", ""))}


def _raw_row(invitation_id) -> str:
    with connection.cursor() as cursor:
        cursor.execute("SELECT * FROM farm_invitations WHERE id = %s", [invitation_id])
        return " ".join(str(value) for value in cursor.fetchone()).upper()


# --- A. Creación ------------------------------------------------------------------

def test_create_sends_code_matching_hash_with_required_content(admin, farm_id, mailbox):
    invitation = _create(admin, farm_id, email="  Invitada@Example.COM ")
    invitation.refresh_from_db()

    assert len(mailbox.messages) == 1
    message = mailbox.messages[0]
    code = _code_in(message)
    assert message["to"] == GUEST
    assert message["subject"] == invitation_mail.SUBJECT
    assert invitation_codes.normalize_code(code)
    assert invitation_codes.hashes_match(invitation.code_hash, _hash(code))
    expiry = invitation_mail.expiry_text(invitation.expires_at)
    for body in (message["text"], message["html"]):
        assert "La Esperanza" in body
        assert "Operador" in body
        assert expiry in body
        assert "Entrar como invitado" in body
        assert "un solo uso" in body
        assert "No lo compartas" in body
    assert invitation.expires_at > timezone.now() + timedelta(days=6, hours=23)


def test_mail_has_no_link_token_hash_or_foreign_data(admin, farm_id, mailbox, fixed_token):
    invitation = _create(admin, farm_id)
    invitation.refresh_from_db()
    message = mailbox.messages[0]
    for body in (message["text"], message["html"]):
        lowered = body.lower()
        assert "http" not in lowered and "href" not in lowered
        assert "/invitations/accept" not in lowered and "#token" not in lowered
        assert fixed_token not in body
        assert invitation.token_hash not in body
        assert invitation.code_hash not in body
        assert admin.correo not in body
        assert admin.identification not in body
        assert "invitation_id" not in lowered


def test_mail_escapes_farm_name_in_html(make_user, mailbox):
    owner = make_user("83000002")
    farm = services.create_farm(user=owner, name="<b>Finca & Co</b>").farm_id
    _create(owner, farm)
    html = mailbox.messages[0]["html"]
    assert "<b>Finca" not in html
    assert "&lt;b&gt;Finca &amp; Co&lt;/b&gt;" in html


def test_database_holds_only_the_hash(admin, farm_id, mailbox):
    invitation = _create(admin, farm_id)
    code = mailbox.codes()[0]
    row = _raw_row(invitation.pk)
    assert code not in row and code.replace("-", "") not in row
    assert _hash(code).upper() in row


def test_api_response_never_contains_code_or_hash(auth_client, user, mailbox):
    farm = auth_client.post("/api/v1/farms/", {"name": "Finca API"}, format="json")
    response = auth_client.post(
        f"/api/v1/farms/{farm.data['id']}/invitations/",
        {"email": GUEST, "role": "consulta"},
        format="json",
    )
    assert response.status_code == 201
    body = response.content.decode()
    code = mailbox.codes()[0]
    for secret in (*_forms([code]), _hash(code)):
        assert secret not in body
    assert "code" not in body.lower()


@pytest.mark.django_db(transaction=True)
def test_mail_is_sent_after_commit(admin, farm_id, mailbox):
    seen_from_other_connection = []

    def check_committed(message):
        code = _code_in(message)

        def read():
            try:
                return FarmInvitation.objects.filter(code_hash=_hash(code)).exists()
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=1) as pool:
            seen_from_other_connection.append(pool.submit(read).result(timeout=10))
        return DeliveryOutcome.ACCEPTED

    mailbox.on_send = check_committed
    _create(admin, farm_id)

    assert mailbox.messages[0]["in_atomic_block"] is False
    assert seen_from_other_connection == [True]


# --- B y C. Reenvío y cambio de rol ---------------------------------------------------

def test_resend_sends_new_code_and_invalidates_previous(admin, farm_id, mailbox):
    invitation = _create(admin, farm_id)
    first_code = mailbox.codes()[0]
    invitation.refresh_from_db()
    first_nonce = invitation.delivery_nonce

    _resend(admin, farm_id, invitation.pk)

    invitation.refresh_from_db()
    second_code = mailbox.codes()[1]
    assert second_code != first_code
    assert invitation.code_hash == _hash(second_code)
    assert not FarmInvitation.objects.filter(code_hash=_hash(first_code)).exists()
    assert invitation.delivery_nonce != first_nonce
    assert invitation.delivery_pending is False
    assert "http" not in mailbox.messages[1]["text"].lower()
    assert _events(farm_id, EventType.INVITATION_RESENT) == 1


def test_change_role_sends_new_code_with_new_role(admin, farm_id, mailbox):
    invitation = _create(admin, farm_id, role=FarmRole.OPERADOR)
    old_code = mailbox.codes()[0]

    _change_role(admin, farm_id, invitation.pk, FarmRole.CONSULTA)

    invitation.refresh_from_db()
    message = mailbox.messages[1]
    new_code = _code_in(message)
    assert invitation.role == FarmRole.CONSULTA
    assert "Consulta" in message["text"] and "Operador" not in message["text"]
    assert invitation.code_hash == _hash(new_code)
    assert not invitation_codes.hashes_match(invitation.code_hash, _hash(old_code))
    assert not FarmInvitation.objects.filter(code_hash=_hash(old_code)).exists()


def test_stale_delivery_cannot_touch_current_delivery(admin, farm_id, mailbox):
    invitation = _create(admin, farm_id)
    stale = FarmInvitation.objects.get(pk=invitation.pk)
    old_code = mailbox.codes()[0]
    _change_role(admin, farm_id, invitation.pk, FarmRole.CONSULTA)
    current = FarmInvitation.objects.get(pk=invitation.pk)
    FarmInvitation.objects.filter(pk=invitation.pk).update(delivery_pending=True)

    for outcome in (DeliveryOutcome.ACCEPTED, DeliveryOutcome.REJECTED):
        mailbox.outcome = outcome
        invitation_services._finalize_delivery(
            invitation=stale,
            farm=current.farm,
            actor=admin,
            code=old_code,
            success_event_type=EventType.INVITATION_RESENT,
        )

    record = FarmInvitation.objects.get(pk=invitation.pk)
    assert record.delivery_pending is True
    assert record.delivery_nonce == current.delivery_nonce
    assert record.delivery_confirmed_at == current.delivery_confirmed_at
    assert record.code_hash == current.code_hash
    assert _events(farm_id, EventType.INVITATION_RESENT) == 0
    assert _events(farm_id, EventType.INVITATION_DELIVERY_FAILED) == 0


# --- D. Entregas fallidas ---------------------------------------------------------

def test_rejected_delivery_keeps_f9_contract_and_recovers_by_resend(admin, farm_id, mailbox):
    mailbox.outcome = DeliveryOutcome.REJECTED
    with pytest.raises(AppError) as info:
        _create(admin, farm_id)

    error = info.value
    invitation = FarmInvitation.objects.get(farm_id=farm_id, email=GUEST)
    failed_code = mailbox.codes()[0]
    assert error.status_code == 502
    assert error.message == invitation_services.INVITATION_DELIVERY_FAILED
    assert error.extra == {"invitation_id": invitation.pk}
    rendered = f"{error} {error!r} {error.extra}"
    for secret in (*_forms([failed_code]), _hash(failed_code)):
        assert secret not in rendered
    assert invitation.status == InvitationStatus.PENDING
    assert invitation.delivery_pending is False
    assert invitation.delivery_confirmed_at is None
    assert invitation.last_sent_at is None
    assert invitation.code_hash == _hash(failed_code)
    assert _events(farm_id, EventType.INVITATION_DELIVERY_FAILED) == 1

    mailbox.outcome = DeliveryOutcome.ACCEPTED
    _resend(admin, farm_id, invitation.pk)

    invitation.refresh_from_db()
    new_code = mailbox.codes()[1]
    assert new_code != failed_code
    assert invitation.code_hash == _hash(new_code)
    assert invitation.delivery_confirmed_at is not None
    assert _events(farm_id, EventType.INVITATION_RESENT) == 1


def test_rejected_delivery_api_body_has_no_secrets(auth_client, user, mailbox):
    mailbox.outcome = DeliveryOutcome.REJECTED
    farm = auth_client.post("/api/v1/farms/", {"name": "Finca 502"}, format="json")
    response = auth_client.post(
        f"/api/v1/farms/{farm.data['id']}/invitations/",
        {"email": GUEST, "role": "operador"},
        format="json",
    )
    assert response.status_code == 502
    assert set(response.data) == {"error", "invitation_id"}
    code = mailbox.codes()[0]
    body = response.content.decode()
    for secret in (*_forms([code]), _hash(code)):
        assert secret not in body


def test_indeterminate_delivery_keeps_reservation(admin, farm_id, mailbox):
    mailbox.outcome = DeliveryOutcome.INDETERMINATE
    invitation = _create(admin, farm_id)
    invitation.refresh_from_db()
    assert invitation.delivery_pending is True
    assert invitation.delivery_confirmed_at is None
    assert invitation.code_hash == _hash(mailbox.codes()[0])
    assert _events(farm_id, EventType.INVITATION_DELIVERY_FAILED) == 0


# --- E. Invitaciones antiguas por enlace -------------------------------------------

def test_legacy_link_invitation_is_untouched_and_still_accepted(admin, farm_id, make_user, mailbox):
    legacy_token = "enlace-antiguo-f1033a"
    legacy = FarmInvitation.objects.create(
        farm_id=farm_id,
        email=GUEST,
        pending_email=GUEST,
        role=FarmRole.OPERADOR,
        token_hash=invitation_services._hash_token(legacy_token),
        expires_at=timezone.now() + timedelta(days=2),
        last_sent_at=timezone.now() - timedelta(days=5),
    )
    before = (legacy.token_hash, legacy.code_hash, legacy.last_sent_at, legacy.expires_at)

    _create(admin, farm_id, email="nueva@example.com")
    invitation_services.list_invitations(user=admin, farm_id=farm_id)
    call_command("expire_farm_invitations", stdout=StringIO())

    legacy.refresh_from_db()
    assert (legacy.token_hash, legacy.code_hash, legacy.last_sent_at, legacy.expires_at) == before
    assert legacy.code_hash is None
    assert [message["to"] for message in mailbox.messages] == ["nueva@example.com"]

    guest = make_user("83000003", correo=GUEST)
    membership = invitation_services.accept_invitation(user=guest, token=legacy_token)
    legacy.refresh_from_db()
    assert membership.role == FarmRole.OPERADOR
    assert legacy.status == InvitationStatus.ACCEPTED
    assert legacy.token_hash == "" and legacy.code_hash is None
    assert len(mailbox.messages) == 1


def test_resending_a_legacy_invitation_switches_it_to_code(admin, farm_id, make_user, mailbox):
    legacy_token = "enlace-antiguo-reenviado"
    legacy = FarmInvitation.objects.create(
        farm_id=farm_id,
        email=GUEST,
        pending_email=GUEST,
        role=FarmRole.OPERADOR,
        token_hash=invitation_services._hash_token(legacy_token),
        expires_at=timezone.now() + timedelta(days=2),
    )
    _resend(admin, farm_id, legacy.pk)
    legacy.refresh_from_db()
    code = mailbox.codes()[0]
    assert legacy.code_hash == _hash(code)
    assert "http" not in mailbox.messages[0]["text"].lower()
    guest = make_user("83000004", correo=GUEST)
    with pytest.raises(AppError) as old_link:
        invitation_services.accept_invitation(user=guest, token=legacy_token)
    assert old_link.value.status_code == 404


# --- F. Seguridad -------------------------------------------------------------------

def test_code_never_reaches_audit_logs_or_sql(admin, farm_id, mailbox, caplog):
    caplog.set_level(logging.DEBUG)
    with CaptureQueriesContext(connection) as queries:
        invitation = _create(admin, farm_id)
        _resend(admin, farm_id, invitation.pk)
        _change_role(admin, farm_id, invitation.pk, FarmRole.CONSULTA)
        other = _create(admin, farm_id, email="otra@example.com")
        invitation_services.cancel_invitation(
            user=admin, farm_id=farm_id, invitation_id=other.pk
        )

    codes = mailbox.codes()
    assert len(codes) == 4
    forms = _forms(codes)
    sql = "\n".join(query["sql"] for query in queries.captured_queries).upper()
    events = json.dumps(
        [
            [e.before_data, e.after_data, e.metadata, e.reason, e.entity_id, e.actor_display]
            for e in AuditEvent.objects.filter(farm_id=farm_id)
        ],
        ensure_ascii=False,
    )
    hashes = {_hash(code) for code in codes[:3]} | {_hash(codes[3], "otra@example.com")}
    for secret in forms:
        assert secret not in sql
        assert secret not in events
        assert secret not in caplog.text
    for digest in hashes:
        assert digest not in events
        assert digest not in caplog.text
        for message in mailbox.messages:
            assert digest not in message["text"] and digest not in message["html"]


def test_error_reports_hide_the_code(admin, farm_id, mailbox, monkeypatch):
    _ISSUED.clear()
    real = invitation_codes.new_code_for

    def spy(email):
        pair = real(email)
        _ISSUED.append(pair[0])
        return pair

    monkeypatch.setattr(invitation_codes, "new_code_for", spy)

    def provider_down(message):
        raise RuntimeError("proveedor no disponible")

    mailbox.on_send = provider_down
    with pytest.raises(RuntimeError):
        try:
            _create(admin, farm_id)
        except RuntimeError:
            reporter = ExceptionReporter(None, *sys.exc_info())
            text = reporter.get_traceback_text()
            frames = reporter.get_traceback_data()["frames"]
            raise

    code = _ISSUED.pop()
    variables = " ".join(str(value) for frame in frames for _name, value in frame["vars"])
    for secret in _forms([code]):
        assert secret not in text
        assert secret not in variables
    # La entrega quedó reservada: el administrador puede reenviar.
    invitation = FarmInvitation.objects.get(farm_id=farm_id, email=GUEST)
    assert invitation.delivery_pending is True


# --- G. Concurrencia -----------------------------------------------------------------

def _in_thread(fn):
    def run():
        try:
            return fn()
        except AppError as exc:
            return exc
        finally:
            connections.close_all()

    return run


@pytest.mark.django_db(transaction=True)
def test_concurrent_resends_leave_one_valid_code(admin, farm_id, mailbox):
    invitation = _create(admin, farm_id)
    barrier = threading.Barrier(2)

    def resend():
        barrier.wait(timeout=5)
        return _resend(admin, farm_id, invitation.pk)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_in_thread(resend)) for _ in range(2)]
        results = [future.result(timeout=20) for future in futures]

    assert all(isinstance(result, FarmInvitation) for result in results)
    invitation.refresh_from_db()
    resent_codes = mailbox.codes()[1:]
    assert len(resent_codes) == 2 and resent_codes[0] != resent_codes[1]
    valid = [code for code in resent_codes if invitation.code_hash == _hash(code)]
    assert len(valid) == 1
    assert invitation.delivery_pending is False
    assert _events(farm_id, EventType.INVITATION_RESEND_STARTED) == 2


@pytest.mark.django_db(transaction=True)
def test_concurrent_role_change_and_resend_keep_role_and_code_consistent(
    admin, farm_id, mailbox
):
    invitation = _create(admin, farm_id, role=FarmRole.OPERADOR)
    barrier = threading.Barrier(2)

    def change():
        barrier.wait(timeout=5)
        return _change_role(admin, farm_id, invitation.pk, FarmRole.CONSULTA)

    def resend():
        barrier.wait(timeout=5)
        return _resend(admin, farm_id, invitation.pk)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_in_thread(change)), pool.submit(_in_thread(resend))]
        results = [future.result(timeout=20) for future in futures]

    assert all(isinstance(result, FarmInvitation) for result in results)
    invitation.refresh_from_db()
    assert invitation.role == FarmRole.CONSULTA
    later = mailbox.messages[1:]
    valid = [m for m in later if invitation.code_hash == _hash(_code_in(m))]
    assert len(valid) == 1
    # El único código válido viaja con el rol vigente; ningún código válido
    # ofrece el rol anterior.
    assert "Consulta" in valid[0]["text"]
    assert not invitation_codes.hashes_match(invitation.code_hash, _hash(mailbox.codes()[0]))


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("late_outcome", [DeliveryOutcome.ACCEPTED, DeliveryOutcome.REJECTED])
def test_old_delivery_finishing_late_does_not_override_new_one(
    admin, farm_id, mailbox, late_outcome
):
    invitation = _create(admin, farm_id)
    slow_entered = threading.Event()
    release_slow = threading.Event()

    def on_send(message):
        if message["thread"] == "slow-delivery":
            slow_entered.set()
            assert release_slow.wait(timeout=20)
            return late_outcome
        return DeliveryOutcome.ACCEPTED

    mailbox.on_send = on_send
    outcome = {}

    def slow():
        try:
            outcome["result"] = _resend(admin, farm_id, invitation.pk)
        except AppError as exc:
            outcome["result"] = exc
        finally:
            connections.close_all()

    worker = threading.Thread(target=slow, name="slow-delivery")
    worker.start()
    try:
        assert slow_entered.wait(timeout=20)  # su transacción ya se confirmó
        _resend(admin, farm_id, invitation.pk)
        current = FarmInvitation.objects.get(pk=invitation.pk)
    finally:
        release_slow.set()
        worker.join(timeout=30)
    assert not worker.is_alive()

    slow_code = next(_code_in(m) for m in mailbox.messages if m["thread"] == "slow-delivery")
    fast_code = mailbox.codes()[-1]
    record = FarmInvitation.objects.get(pk=invitation.pk)
    assert record.code_hash == _hash(fast_code) != _hash(slow_code)
    assert record.delivery_nonce == current.delivery_nonce
    assert record.delivery_pending is False
    assert record.delivery_confirmed_at == current.delivery_confirmed_at
    assert record.last_sent_at == current.last_sent_at
    assert _events(farm_id, EventType.INVITATION_RESENT) == 1
    assert _events(farm_id, EventType.INVITATION_DELIVERY_FAILED) == 0
    if late_outcome == DeliveryOutcome.REJECTED:
        # La respuesta 502 corresponde a la entrega antigua, pero no alteró la vigente.
        assert isinstance(outcome["result"], AppError)
        assert outcome["result"].status_code == 502
    else:
        assert isinstance(outcome["result"], FarmInvitation)

