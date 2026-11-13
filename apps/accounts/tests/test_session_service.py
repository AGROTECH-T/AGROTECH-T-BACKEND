"""Prueba los servicios de sesiones sobre el modelo multi-dispositivo.

@author Cristian Deysdayr Jimenez
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from django.db import connections
from django.utils import timezone

from apps.accounts.errors import AppError
from apps.accounts.models import Session, User
from apps.accounts.services import session_service, token_service


def _make_user(identification: str) -> User:
    """Crea una cuenta adicional con el identificador indicado."""
    return User.objects.create(
        first_name="Otro",
        last_name="Usuario",
        identification=identification,
        password_hash="x",
    )


def _open(user: User, compact: str | None = None) -> Session:
    """Abre una sesión con un token válido (o el compacto indicado)."""
    token = compact or token_service.issue(user.pk)
    return session_service.open_session(user, token)


# --- open_session ---


@pytest.mark.django_db
def test_open_session_creates_a_row(user):
    token = token_service.issue(user.pk)
    session = session_service.open_session(user, token)
    assert session.pk is not None
    assert Session.objects.filter(pk=session.pk).count() == 1


@pytest.mark.django_db
def test_open_session_sets_created_at_and_last_seen(user):
    session = _open(user)
    assert session.created_at is not None
    assert session.last_seen is not None
    assert session.created_at == session.last_seen
    assert timezone.now() - session.created_at < timedelta(minutes=1)


@pytest.mark.django_db
def test_open_session_stores_expected_digest(user):
    token = token_service.issue(user.pk)
    session = session_service.open_session(user, token)
    assert session.token_hash == token_service.digest(token)


@pytest.mark.django_db
def test_open_session_does_not_store_plain_token(user):
    token = token_service.issue(user.pk)
    session = session_service.open_session(user, token)
    assert session.token_hash != token
    assert token not in session.token_hash


@pytest.mark.django_db
def test_multiple_sessions_per_user_are_allowed(user):
    _open(user)
    _open(user)
    _open(user)
    assert Session.objects.filter(user=user).count() == 3


# --- authenticate ---


@pytest.mark.django_db
def test_authenticate_valid(user):
    token = token_service.issue(user.pk)
    _open(user, token)
    authenticated_user, claims = session_service.authenticate(token)
    assert authenticated_user.pk == user.pk
    assert claims.user_id == user.pk


@pytest.mark.django_db
def test_authenticate_rejects_closed_session(user):
    token = token_service.issue(user.pk)
    session = _open(user, token)
    session_service.revoke_session(session)
    with pytest.raises(AppError) as exc:
        session_service.authenticate(token)
    assert exc.value.status_code == 401


@pytest.mark.django_db
def test_authenticate_rejects_inactive_user(user):
    token = token_service.issue(user.pk)
    _open(user, token)
    user.is_active = False
    user.save(update_fields=["is_active"])
    with pytest.raises(AppError) as exc:
        session_service.authenticate(token)
    assert exc.value.status_code == 401


@pytest.mark.django_db
def test_authenticate_rejects_expired_token(user):
    token = token_service.issue(user.pk)
    session = _open(user, token)
    stale = timezone.now() - token_service.ttl() - timedelta(minutes=1)
    Session.objects.filter(pk=session.pk).update(last_seen=stale)
    with pytest.raises(AppError) as exc:
        session_service.authenticate(token)
    assert exc.value.status_code == 401


# --- authenticate_by_session_key ---


@pytest.mark.django_db
def test_authenticate_by_session_key_valid(user):
    token = token_service.issue(user.pk)
    session = session_service.open_session(user, token, session_key="abc123")
    assert session_service.authenticate_by_session_key("abc123").pk == session.pk


@pytest.mark.django_db
def test_authenticate_by_session_key_rejects_closed(user):
    token = token_service.issue(user.pk)
    session = session_service.open_session(user, token, session_key="abc123")
    session_service.revoke_session(session)
    assert session_service.authenticate_by_session_key("abc123") is None


@pytest.mark.django_db
def test_authenticate_by_session_key_rejects_inactive_user(user):
    token = token_service.issue(user.pk)
    session_service.open_session(user, token, session_key="abc123")
    user.is_active = False
    user.save(update_fields=["is_active"])
    assert session_service.authenticate_by_session_key("abc123") is None


# --- rotate (CAS) ---


@pytest.mark.django_db
def test_rotate_preserves_pk(user):
    token = token_service.issue(user.pk)
    session = _open(user, token)
    session_service.rotate(token)
    assert Session.objects.filter(pk=session.pk).exists()
    assert Session.objects.filter(user=user).count() == 1


@pytest.mark.django_db
def test_rotate_updates_token_hash(user):
    token = token_service.issue(user.pk)
    session = _open(user, token)
    new_token = session_service.rotate(token)
    session.refresh_from_db()
    assert session.token_hash == token_service.digest(new_token)
    assert session.token_hash != token_service.digest(token)


@pytest.mark.django_db
def test_rotate_invalidates_previous_token(user):
    token = token_service.issue(user.pk)
    _open(user, token)
    session_service.rotate(token)
    with pytest.raises(AppError) as exc:
        session_service.authenticate(token)
    assert exc.value.status_code == 401


@pytest.mark.django_db
def test_rotate_refreshes_last_seen(user):
    token = token_service.issue(user.pk)
    session = _open(user, token)
    stale = timezone.now() - timedelta(minutes=10)
    Session.objects.filter(pk=session.pk).update(last_seen=stale)
    session_service.rotate(token)
    session.refresh_from_db()
    assert session.last_seen > stale
    assert timezone.now() - session.last_seen < timedelta(minutes=1)


@pytest.mark.django_db
def test_cas_loser_returns_401(user, monkeypatch):
    token = token_service.issue(user.pk)
    session = _open(user, token)
    winner = token_service.issue(user.pk)
    Session.objects.filter(pk=session.pk).update(token_hash=token_service.digest(winner))

    stale = Session.objects.select_related("user").get(pk=session.pk)
    monkeypatch.setattr(session_service, "_find_open_session", lambda *a, **k: stale)

    with pytest.raises(AppError) as exc:
        session_service.rotate(token)
    assert exc.value.status_code == 401
    session.refresh_from_db()
    assert session.token_hash == token_service.digest(winner)


@pytest.mark.django_db
def test_cas_loser_does_not_return_token(user, monkeypatch):
    token = token_service.issue(user.pk)
    session = _open(user, token)
    winner = token_service.issue(user.pk)
    Session.objects.filter(pk=session.pk).update(token_hash=token_service.digest(winner))

    stale = Session.objects.select_related("user").get(pk=session.pk)
    monkeypatch.setattr(session_service, "_find_open_session", lambda *a, **k: stale)

    with pytest.raises(AppError):
        session_service.rotate(token)


@pytest.mark.django_db(transaction=True)
def test_concurrent_rotations_have_single_winner(user):
    token = token_service.issue(user.pk)
    _open(user, token)

    barrier = Barrier(2)
    results = []

    def run():
        barrier.wait(timeout=5)
        try:
            try:
                next_token = session_service.rotate(token)
                results.append(("ok", next_token))
            except AppError as exc:
                results.append(("error", exc.status_code))
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run) for _ in range(2)]
        for future in futures:
            future.result(timeout=10)

    assert [result[0] for result in results].count("ok") == 1
    assert [result[0] for result in results].count("error") == 1
    errors = [result for result in results if result[0] == "error"]
    assert errors[0][1] == 401
    assert Session.objects.filter(user=user).count() == 1
    winner = [result for result in results if result[0] == "ok"][0]
    assert Session.objects.get(user=user).token_hash == token_service.digest(winner[1])


@pytest.mark.django_db
def test_revoke_first_then_rotate_does_not_reopen(user):
    token = token_service.issue(user.pk)
    session = _open(user, token)
    assert session_service.revoke_session(session) is True
    with pytest.raises(AppError) as exc:
        session_service.rotate(token)
    assert exc.value.status_code == 401
    session.refresh_from_db()
    assert session.closed_at is not None


@pytest.mark.django_db
def test_rotate_first_then_revoke_ends_closed(user):
    token = token_service.issue(user.pk)
    session = _open(user, token)
    new_token = session_service.rotate(token)
    session.refresh_from_db()
    assert session.closed_at is None
    assert session_service.revoke_session(session) is True
    session.refresh_from_db()
    assert session.closed_at is not None
    with pytest.raises(AppError):
        session_service.authenticate(new_token)


@pytest.mark.django_db
def test_rotate_does_not_create_duplicate_rows(user):
    token = token_service.issue(user.pk)
    _open(user, token)
    session_service.rotate(token)
    assert Session.objects.filter(user=user).count() == 1


# --- revocación ---


@pytest.mark.django_db
def test_revoke_session_first_call_returns_true(user):
    session = _open(user)
    assert session_service.revoke_session(session) is True
    session.refresh_from_db()
    assert session.closed_at is not None


@pytest.mark.django_db
def test_revoke_session_repeated_returns_false(user):
    session = _open(user)
    session_service.revoke_session(session)
    assert session_service.revoke_session(session) is False


@pytest.mark.django_db
def test_revoke_other_sessions_excludes_current(user):
    first = _open(user)
    current = _open(user)
    third = _open(user)
    assert session_service.revoke_other_sessions(current) == 2
    first.refresh_from_db()
    current.refresh_from_db()
    third.refresh_from_db()
    assert first.closed_at is not None
    assert current.closed_at is None
    assert third.closed_at is not None


@pytest.mark.django_db
def test_revoke_all_closes_all_open_sessions(user):
    _open(user)
    _open(user)
    assert session_service.revoke_all(user) == 2
    assert not Session.objects.filter(user=user, closed_at__isnull=True).exists()


@pytest.mark.django_db
def test_closed_rows_are_preserved(user):
    session = _open(user)
    session_service.revoke_all(user)
    assert Session.objects.filter(pk=session.pk).exists()
    session.refresh_from_db()
    assert session.closed_at is not None


@pytest.mark.django_db
def test_legacy_sessions_with_null_key_are_preserved(user):
    legacy = Session.objects.create(
        user=user,
        session_key=None,
        token_hash=token_service.digest("legacy-token"),
        last_seen=timezone.now(),
        created_at=timezone.now(),
    )
    active = _open(user)
    assert session_service.authenticate_by_session_key(None) is None
    session_service.revoke_all(user)
    assert Session.objects.filter(pk=legacy.pk).exists()
    assert Session.objects.filter(pk=active.pk).exists()


@pytest.mark.django_db
def test_token_hash_collision_raises_controlled_error(user, monkeypatch):
    token = token_service.issue(user.pk)
    _open(user, token)

    other = _make_user("87654321")
    colliding = "colliding-token-value"
    Session.objects.create(
        user=other,
        token_hash=token_service.digest(colliding),
        last_seen=timezone.now(),
        created_at=timezone.now(),
    )

    monkeypatch.setattr(token_service, "issue", lambda *args, **kwargs: colliding)

    with pytest.raises(AppError) as exc:
        session_service.rotate(token)
    assert exc.value.status_code == 500
    assert exc.value.message != "la sesion vencio"
