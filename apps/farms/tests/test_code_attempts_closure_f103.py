# ruff: noqa: S101
"""Cierre F10.3: regresiones de D-1, D-2 y D-3 (contador de códigos y throttles).

Propósito: fijar las garantías corregidas tras F10.3.4-B:
- D-1: la resolución de una reserva antigua nunca modifica una ventana nueva,
  ocurre como mucho una vez y no produce valores negativos.
- D-2: los throttles ``invitation_accept`` (10/hora por cuenta) e
  ``invitation_accept_ip`` (30/hora por IP) no superan su cuota bajo
  concurrencia.
- D-3: una solicitud rechazada no consume unidad, un 200/409 devuelve la suya y
  una solicitud bloqueada solo por intentos en curso espera un hueco.

Contexto: se ejecutan con la caché configurada. En la suite normal es
``LocMemCache`` (un proceso, cerrojo); con ``-Validate -WithRedis`` es Redis
real (scripts Lua). Las variantes con procesos independientes y Redis real
están en ``test_code_attempts_redis_f1034b.py``.
"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from django.core.cache import cache
from rest_framework.exceptions import Throttled

from apps.farms import code_attempts
from apps.farms.atomic_cache import raw_key, redis_client
from apps.farms.throttles import InvitationAcceptAccountThrottle, InvitationAcceptIpThrottle

pytestmark = pytest.mark.django_db

MAX = code_attempts.MAX_FAILED_ATTEMPTS


@pytest.fixture
def account(make_user):
    return make_user("84350001", correo="cierre-f103@example.com")


def _window(user_pk):
    name = f"farms:invitation-code-attempts:{user_pk}:window"
    client = redis_client()
    if client is None:
        state = cache.get(name) or {"f": 0, "p": 0}
        return int(state["f"]), int(state["p"])
    failures, pending = client.hmget(raw_key(name), ["f", "p"])
    return int(failures or 0), int(pending or 0)


def _expire_window(user_pk):
    """Vence la ventana actual (Redis: PEXPIRE real; LocMemCache: la clave desaparece)."""
    name = f"farms:invitation-code-attempts:{user_pk}:window"
    client = redis_client()
    if client is None:
        cache.delete(name)
        return
    client.pexpire(raw_key(name), 1)
    time.sleep(0.05)
    assert client.exists(raw_key(name)) == 0


def _try_begin(user_pk):
    guard = code_attempts.CodeAttemptGuard(user_pk)
    try:
        guard.begin()
    except Throttled as exc:
        return None, exc
    return guard, None


# --- D-1 ----------------------------------------------------------------------------------

@pytest.mark.parametrize("stale_outcome", ["not_failed", "failed"])
def test_d1_stale_reservation_never_touches_the_new_window(account, stale_outcome):
    old = code_attempts.CodeAttemptGuard(account.pk)
    old.begin()
    _expire_window(account.pk)
    new = code_attempts.CodeAttemptGuard(account.pk)
    new.begin()
    new.failed()
    assert _window(account.pk) == (1, 0)
    getattr(old, stale_outcome)()  # la petición de la ventana vencida termina tarde
    assert _window(account.pk) == (1, 0)
    allowed = 0
    while True:
        guard, error = _try_begin(account.pk)
        if error:
            break
        guard.failed()
        allowed += 1
    assert 1 + allowed == MAX  # nunca más de 5 fallos evaluados en la ventana nueva


def test_d1_resolution_happens_at_most_once_and_never_goes_negative(account):
    guard = code_attempts.CodeAttemptGuard(account.pk)
    guard.begin()
    window, token = guard._window, guard._token
    guard.not_failed()
    guard.not_failed()
    guard.failed()
    code_attempts._resolve(account.pk, window, token, failed=False)  # token ya resuelto
    code_attempts._resolve(account.pk, window, token, failed=True)
    code_attempts._resolve(account.pk, window, "token-ajeno", failed=False)
    code_attempts._resolve(account.pk, "ventana-ajena", token, failed=False)
    assert _window(account.pk) == (0, 0)
    assert code_attempts.failed_attempts(account.pk) == 0


def test_d1_failure_is_recorded_once(account):
    guard = code_attempts.CodeAttemptGuard(account.pk)
    guard.begin()
    guard.failed()
    guard.failed()
    guard.not_failed()
    assert _window(account.pk) == (1, 0)


# --- D-3 ----------------------------------------------------------------------------------

def test_d3_rejected_requests_do_not_consume_units(account):
    for _ in range(MAX):
        guard, _error = _try_begin(account.pk)
        guard.failed()
    for _ in range(3):
        guard, error = _try_begin(account.pk)
        assert guard is None and error is not None
        assert error.wait > 60  # bloqueo por fallos: espera hasta el fin de la ventana
    assert _window(account.pk) == (MAX, 0)


def test_d3_valid_results_return_their_unit(account):
    for _ in range(MAX + 3):
        guard, error = _try_begin(account.pk)
        assert error is None
        guard.not_failed()
    assert _window(account.pk) == (0, 0)


def test_d3_request_blocked_only_by_pending_waits_for_a_slot(account):
    held = []
    for _ in range(MAX):
        guard, _error = _try_begin(account.pk)
        held.append(guard)
    assert _window(account.pk) == (0, MAX)

    def release_one_later():
        time.sleep(0.3)
        held[0].not_failed()

    releaser = threading.Thread(target=release_one_later)
    releaser.start()
    started = time.monotonic()
    guard, error = _try_begin(account.pk)
    releaser.join()
    assert error is None and guard is not None
    assert 0.2 <= time.monotonic() - started < code_attempts.PENDING_WAIT_SECONDS
    for item in held[1:] + [guard]:
        item.not_failed()
    assert _window(account.pk) == (0, 0)


def test_d3_pending_that_never_resolves_gives_short_429(account, monkeypatch):
    monkeypatch.setattr(code_attempts, "PENDING_WAIT_SECONDS", 0.2)
    for _ in range(MAX):
        _try_begin(account.pk)
    guard, error = _try_begin(account.pk)
    assert guard is None
    assert error.wait == 1  # reintento breve: no son fallos confirmados
    assert _window(account.pk) == (0, MAX)


def test_d3_concurrent_mixed_burst_counts_only_real_failures(account):
    """Ráfaga con hilos: 6 válidos y 4 fallos; el contador final es 4."""
    barrier = threading.Barrier(10)

    def attempt(valid):
        barrier.wait(timeout=10)
        guard, error = _try_begin(account.pk)
        if error:
            return "blocked"
        time.sleep(0.05)  # evaluación del código
        if valid:
            guard.not_failed()
            return "ok"
        guard.failed()
        return "fail"

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(attempt, [True] * 6 + [False] * 4))
    assert results.count("ok") == 6
    assert results.count("fail") == 4
    assert _window(account.pk) == (4, 0)


# --- D-2 ----------------------------------------------------------------------------------

def _request(user, ip="10.40.0.1", forwarded=None):
    meta = {"REMOTE_ADDR": ip}
    if forwarded:
        meta["HTTP_X_FORWARDED_FOR"] = forwarded
    return SimpleNamespace(user=user, META=meta)


VIEW = SimpleNamespace(throttle_scope="invitation_accept")


def test_d2_account_throttle_keeps_ten_per_hour(account):
    throttle_results = []
    for index in range(12):
        throttle = InvitationAcceptAccountThrottle()
        throttle_results.append(throttle.allow_request(_request(account, ip=f"10.41.0.{index}"), VIEW))
    assert throttle_results == [True] * 10 + [False] * 2
    assert 3500 <= throttle.wait() <= 3600


def test_d2_ip_throttle_keeps_thirty_per_hour_and_ignores_forwarded_for(make_user):
    users = [make_user(f"8436{index:04d}", correo=f"ip-cierre{index}@example.com") for index in range(32)]
    allowed = []
    for index, user in enumerate(users):
        throttle = InvitationAcceptIpThrottle()
        allowed.append(throttle.allow_request(
            _request(user, ip="203.0.113.50", forwarded=f"198.51.100.{index}"), VIEW
        ))
    assert allowed == [True] * 30 + [False] * 2
    assert InvitationAcceptIpThrottle().allow_request(_request(users[0], ip="203.0.113.51"), VIEW)


def test_d2_account_throttle_is_atomic_under_concurrency(account):
    barrier = threading.Barrier(20)

    def attempt(index):
        barrier.wait(timeout=10)
        return InvitationAcceptAccountThrottle().allow_request(
            _request(account, ip=f"10.42.0.{index}"), VIEW
        )

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(attempt, range(20)))
    assert results.count(True) == 10


def test_d2_view_without_scope_is_not_limited(account):
    throttle = InvitationAcceptAccountThrottle()
    assert throttle.allow_request(_request(account), SimpleNamespace()) is True


def test_invitation_views_use_the_atomic_throttles():
    from apps.farms.views import (
        FarmInvitationAcceptView,
        FarmInvitationCodeAcceptView,
        FarmInvitationCodePreviewView,
    )

    for view in (FarmInvitationAcceptView, FarmInvitationCodePreviewView, FarmInvitationCodeAcceptView):
        assert view.throttle_classes == [InvitationAcceptAccountThrottle, InvitationAcceptIpThrottle]
        assert view.throttle_scope == "invitation_accept"
