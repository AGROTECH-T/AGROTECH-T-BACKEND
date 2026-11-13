# ruff: noqa: S101, S105, S106
"""F10.3.4-B: contador de intentos de códigos con Redis REAL y procesos independientes.

Propósito: demostrar con el Redis desechable del orquestador (``-Validate
-WithRedis``) que el contador de ``apps/farms/code_attempts.py`` se comparte
entre procesos, que el límite de 5 fallos no se supera bajo concurrencia real,
que preview y accept comparten la cuota, que los intentos válidos devuelven su
unidad y que el TTL de la ventana se conserva y vence. Además reproduce de
forma determinista la carrera D-1, mide el throttle de DRF bajo concurrencia
(D-2) y observa el comportamiento con Redis en pausa (F).

Contexto: solo tiene sentido con Redis real; con LocMemCache todas se omiten.
Los procesos independientes son ``python -m apps.farms.tests.f1034b_redis_worker``
(otros procesos del sistema operativo), que demuestran por su cuenta la
identidad de la MariaDB y del Redis desechables. Datos sintéticos, correo
sustituido por un buzón en memoria y clave HMAC ficticia por prueba. La
evidencia (sin secretos) se añade a ``f1034b-evidence.jsonl`` en el
directorio de informes del orquestador, fuera del repositorio.

Las pruebas D-1 y D-2 documentan el comportamiento ACTUAL observado; no
corrigen nada.
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest
from django.conf import settings as django_settings
from django.core.cache import cache
from rest_framework.exceptions import Throttled
from rest_framework.test import APIClient

from apps.accounts.integrations.delivery import DeliveryOutcome
from apps.farms import code_attempts, invitation_services, services
from apps.farms.models import FarmInvitation, FarmMembership, FarmRole, InvitationStatus

REDIS_BACKEND = "django_redis.cache.RedisCache"
REDIS_ON = django_settings.CACHES.get("default", {}).get("BACKEND") == REDIS_BACKEND

pytestmark = [
    pytest.mark.skipif(
        not REDIS_ON, reason="requiere el Redis desechable real (-Validate -WithRedis)"
    ),
    pytest.mark.django_db(transaction=True),
]

PREVIEW = "/api/v1/invitations/code/preview/"
ACCEPT = "/api/v1/invitations/code/accept/"
WRONG = "ZZZZZ-ZZZZZ"
_GROUP = "[ABCDEFGHJKMNPQRSTUVWXYZ23456789]{5}"
CODE_RE = re.compile(rf"\b{_GROUP}-{_GROUP}\b")
WORKER = "apps.farms.tests.f1034b_redis_worker"
MAX = code_attempts.MAX_FAILED_ATTEMPTS


# --- Utilidades -------------------------------------------------------------------------

def _guard_module():
    import config.settings_test_disposable as guard

    return guard


def _raw(db=0, password=True):
    """Cliente directo al Redis desechable (destino fijo de la configuración)."""
    import redis

    guard = _guard_module()
    return redis.Redis(
        host=guard.DEFAULT_TEST_REDIS_HOST,
        port=int(guard.DEFAULT_TEST_REDIS_PORT),
        db=db,
        password=os.environ[guard.DISPOSABLE_REDIS_CREDENTIAL_ENV] if password else None,
        socket_timeout=5,
        socket_connect_timeout=5,
    )


def _key(user_pk, suffix):
    """Claves del contador (cierre F10.3): ``window`` (hash) y ``reservations`` (set)."""
    return cache.make_key(f"farms:invitation-code-attempts:{user_pk}:{suffix}")


def _raw_window(user_pk):
    failures, pending = _raw().hmget(_key(user_pk, "window"), ["f", "p"])
    return int(failures or 0), int(pending or 0)


def _raw_count(user_pk):
    """Unidades en uso leídas directamente de Redis: fallos + intentos en curso."""
    failures, pending = _raw_window(user_pk)
    return failures + pending


def _evidence(name, payload):
    report_dir = os.environ.get("AGROTECH_DISPOSABLE_REPORT_DIR")
    if not report_dir:
        return
    line = json.dumps({"test": name, **payload}, ensure_ascii=False, default=str)
    with open(Path(report_dir) / "f1034b-evidence.jsonl", "a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _spawn(user_pk, task_lists, timeout=180):
    """Lanza un proceso independiente por lista de tareas y los suelta a la vez.

    Cada proceso arranca Django, verifica MariaDB y Redis desechables y
    responde READY; cuando todos están listos se les envía GO casi a la vez.
    """
    base_env = dict(os.environ)
    base_env["F1034B_USER_PK"] = str(user_pk)
    base_env["F1034B_HMAC_KEY"] = django_settings.INVITATION_CODE_HMAC_KEY
    procs = []
    for tasks in task_lists:
        env = dict(base_env, F1034B_TASKS=json.dumps(tasks))
        procs.append(
            subprocess.Popen(
                [sys.executable, "-B", "-s", "-X", "utf8", "-m", WORKER],
                cwd=str(django_settings.BASE_DIR),
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
            )
        )
    ready = []
    try:
        for proc in procs:
            line = proc.stdout.readline()
            assert line.startswith("READY "), f"el trabajador no arrancó: {line!r} {proc.stderr.read()[-2000:]}"
            ready.append(json.loads(line[6:]))
        for proc in procs:
            proc.stdin.write("GO\n")
            proc.stdin.flush()
        results = []
        for proc in procs:
            out, err = proc.communicate(timeout=timeout)
            result_line = [ln for ln in out.splitlines() if ln.startswith("RESULT ")]
            assert proc.returncode == 0 and result_line, f"trabajador falló: {out[-1000:]} {err[-2000:]}"
            results.append(json.loads(result_line[-1][7:]))
    finally:
        for proc in procs:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
    pids = {item["pid"] for item in ready}
    assert len(pids) == len(procs) and os.getpid() not in pids
    for item in ready:
        assert item["cache_location"] == "redis://127.0.0.1:16379/0"
    return ready, results


def _statuses(results):
    return [r["status"] for res in results for r in res["results"] if r["op"] == "post"]


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
    return make_user("84340001", correo="admin-f1034b@example.com")


@pytest.fixture
def farm_id(admin):
    return services.create_farm(user=admin, name="Finca F1034B").farm_id


def _invite(admin, farm_id, mailbox, email):
    invitation = invitation_services.create_invitation(
        user=admin, farm_id=farm_id, email=email, role=FarmRole.OPERADOR
    )
    return invitation, mailbox[-1]["code"]


# --- Identidad del Redis real y caché de Django -----------------------------------------

def test_redis_identity_and_django_cache_use_the_disposable_redis():
    guard = _guard_module()
    default = django_settings.CACHES["default"]
    assert set(django_settings.CACHES) == {"default"}
    assert default["BACKEND"] == REDIS_BACKEND
    assert default["LOCATION"] == "redis://127.0.0.1:16379/0"
    assert not default["OPTIONS"].get("IGNORE_EXCEPTIONS")
    assert not getattr(django_settings, "DJANGO_REDIS_IGNORE_EXCEPTIONS", False)

    # Lo que escribe Django lo lee un cliente Redis directo (misma base 0).
    cache.set("f1034b:probe", 4242, 30)
    assert _raw().get(cache.make_key("f1034b:probe")) == b"4242"

    info = _raw().info("server")
    assert info["redis_version"] == "7.4.12"
    assert str(info["tcp_port"]) == "6379"
    persistence = {
        "save": _raw().config_get("save")["save"],
        "appendonly": _raw().config_get("appendonly")["appendonly"],
        "databases": _raw().config_get("databases")["databases"],
    }
    assert persistence == {"save": "", "appendonly": "no", "databases": "2"}
    assert _raw(db=guard.TEST_REDIS_MARKER_DB).get(guard.REDIS_MARKER_KEY).decode() == (
        os.environ["AGROTECH_DISPOSABLE_RUN_ID"]
    )

    import redis

    with pytest.raises(redis.exceptions.AuthenticationError):
        _raw(password=False).ping()
    _evidence(
        "identity",
        {
            "redis_version": info["redis_version"],
            "redis_mode": info.get("redis_mode"),
            "tcp_port": info["tcp_port"],
            "os": info.get("os"),
            "arch_bits": info.get("arch_bits"),
            "persistence": persistence,
            "unauthenticated": "AuthenticationError (NOAUTH)",
            "cache_location": default["LOCATION"],
        },
    )


# --- A. Contador compartido entre procesos ----------------------------------------------

def test_a_counter_is_shared_between_independent_processes(admin, farm_id, make_user, mailbox):
    guest = make_user("84340010", correo="a-f1034b@example.com")
    invitation, code = _invite(admin, farm_id, mailbox, guest.correo)

    ready_a, res_a = _spawn(guest.pk, [[
        {"op": "post", "url": PREVIEW, "code": WRONG, "ip": "10.31.0.1"},
        {"op": "post", "url": PREVIEW, "code": WRONG, "ip": "10.31.0.1"},
        {"op": "post", "url": PREVIEW, "code": WRONG, "ip": "10.31.0.1"},
        {"op": "count"},
    ]])
    assert _statuses(res_a) == [404, 404, 404]
    assert res_a[0]["results"][-1]["count"] == 3

    ready_b, res_b = _spawn(guest.pk, [[
        {"op": "count"},
        {"op": "post", "url": ACCEPT, "code": WRONG, "ip": "10.31.0.2"},
        {"op": "post", "url": ACCEPT, "code": WRONG, "ip": "10.31.0.2"},
        {"op": "count"},
        {"op": "post", "url": PREVIEW, "code": code, "ip": "10.31.0.2"},
    ]])
    steps = res_b[0]["results"]
    assert steps[0]["count"] == 3  # B ve el estado que dejó A
    assert [s["status"] for s in steps if s["op"] == "post"] == [404, 404, 429]
    assert steps[3]["count"] == MAX
    assert ready_a[0]["pid"] != ready_b[0]["pid"]

    # El proceso de pytest y un cliente Redis directo ven el mismo contador; el
    # intento bloqueado no consume unidad (cierre F10.3, D-3).
    assert code_attempts.failed_attempts(guest.pk) == _raw_count(guest.pk) == MAX
    assert _raw_window(guest.pk) == (MAX, 0)
    invitation.refresh_from_db()
    assert invitation.status == InvitationStatus.PENDING
    assert not FarmMembership.objects.filter(farm_id=farm_id, user=guest).exists()
    _evidence(
        "A",
        {
            "pid_pytest": os.getpid(), "pid_a": ready_a[0]["pid"], "pid_b": ready_b[0]["pid"],
            "a_statuses": _statuses(res_a), "b_steps": steps,
            "redis_count_after": _raw_count(guest.pk),
        },
    )


# --- B. Atomicidad con procesos concurrentes --------------------------------------------

def test_b_concurrent_guard_begins_from_processes_never_exceed_the_limit(make_user):
    rounds = []
    for index in range(3):
        user = make_user(f"8434002{index}", correo=f"b{index}-f1034b@example.com")
        processes = 16
        ready, results = _spawn(user.pk, [[{"op": "begin"}] for _ in range(processes)])
        outcomes = [r["results"][0]["result"] for r in results]
        rounds.append({
            "pids": sorted(item["pid"] for item in ready),
            "allowed": outcomes.count("allowed"),
            "blocked": outcomes.count("blocked"),
            "redis_count": _raw_count(user.pk),
        })
        assert outcomes.count("allowed") == MAX
        assert outcomes.count("blocked") == processes - MAX
        # Solo las 5 reservas permitidas están en uso; las rechazadas no suman.
        assert _raw_window(user.pk) == (0, MAX)
    _evidence("B-guard", {"rounds": rounds})


def test_b_concurrent_http_failures_from_processes_get_exactly_five_404(
    admin, farm_id, make_user, mailbox
):
    user = make_user("84340030", correo="bh-f1034b@example.com")
    _invite(admin, farm_id, mailbox, user.correo)
    processes = 10  # dentro de la cuota de DRF (10/hora por cuenta)
    ready, results = _spawn(
        user.pk,
        [[{"op": "post", "url": PREVIEW if i % 2 else ACCEPT, "code": WRONG, "ip": f"10.32.0.{i}"}]
         for i in range(processes)],
    )
    statuses = _statuses(results)
    assert statuses.count(404) == MAX
    assert statuses.count(429) == processes - MAX
    assert _raw_window(user.pk) == (MAX, 0)
    _evidence("B-http", {"statuses": sorted(statuses), "redis_count": _raw_count(user.pk),
                         "pids": sorted(item["pid"] for item in ready)})


# --- C. Cuota compartida entre preview y accept -----------------------------------------

@pytest.mark.parametrize(
    "order",
    [(PREVIEW, PREVIEW, ACCEPT, ACCEPT, PREVIEW, ACCEPT), (ACCEPT, ACCEPT, PREVIEW, PREVIEW, ACCEPT, PREVIEW)],
    ids=["preview-luego-accept", "accept-luego-preview"],
)
def test_c_preview_and_accept_share_one_counter_across_processes(
    admin, farm_id, make_user, mailbox, order
):
    suffix = "1" if order[0] == PREVIEW else "2"
    user = make_user(f"8434004{suffix}", correo=f"c{suffix}-f1034b@example.com")
    invitation, code = _invite(admin, farm_id, mailbox, user.correo)
    seen = []
    for step, url in enumerate(order[:MAX]):
        _ready, res = _spawn(user.pk, [[
            {"op": "post", "url": url, "code": WRONG, "ip": f"10.33.{suffix}.{step}"},
            {"op": "count"},
        ]])
        seen.append((url, res[0]["results"][0]["status"], res[0]["results"][1]["count"]))
    assert [s[1] for s in seen] == [404] * MAX
    assert [s[2] for s in seen] == [1, 2, 3, 4, 5]
    _ready, final = _spawn(user.pk, [[{"op": "post", "url": order[MAX], "code": code, "ip": "10.33.9.9"}]])
    assert _statuses(final) == [429]
    invitation.refresh_from_db()
    assert invitation.status == InvitationStatus.PENDING
    assert not FarmMembership.objects.filter(farm_id=farm_id, user=user).exists()
    _evidence("C", {"order": [u.rsplit("/", 2)[-2] for u in order], "steps": seen,
                    "final_status_valid_code": _statuses(final)[0]})


# --- D. Intentos válidos (200 y 409) devuelven su unidad --------------------------------

def _valid_and_member_codes(admin, farm_id, mailbox, user):
    _invitation, code = _invite(admin, farm_id, mailbox, user.correo)
    farm2 = services.create_farm(user=admin, name=f"Finca F1034B {user.pk}").farm_id
    _inv2, code_member = _invite(admin, farm2, mailbox, user.correo)
    FarmMembership.objects.create(farm_id=farm2, user=user, role=FarmRole.CONSULTA)
    return code, code_member


def _d_tasks(code, code_member, valid, member, wrong, net):
    return (
        [[{"op": "post", "url": PREVIEW, "code": code, "ip": f"10.{net}.0.{i}"}, {"op": "count"}] for i in range(valid)]
        + [[{"op": "post", "url": PREVIEW, "code": code_member, "ip": f"10.{net}.1.{i}"}, {"op": "count"}] for i in range(member)]
        + [[{"op": "post", "url": PREVIEW, "code": WRONG, "ip": f"10.{net}.2.{i}"}, {"op": "count"}] for i in range(wrong)]
    )


def test_d_valid_attempts_return_their_unit_with_five_in_flight(admin, farm_id, make_user, mailbox):
    """Con como máximo 5 solicitudes en curso, 200 y 409 devuelven su unidad."""
    user = make_user("84340050", correo="d-f1034b@example.com")
    code, code_member = _valid_and_member_codes(admin, farm_id, mailbox, user)
    _ready, results = _spawn(user.pk, _d_tasks(code, code_member, valid=2, member=1, wrong=2, net=34))
    statuses = _statuses(results)
    counts = [r["results"][1]["count"] for r in results]
    assert sorted(statuses) == [200, 200, 404, 404, 409]
    assert all(value >= 0 for value in counts)
    assert code_attempts.failed_attempts(user.pk) == _raw_count(user.pk) == 2
    _evidence("D-5", {"statuses": sorted(statuses), "counts_seen_by_processes": counts,
                      "redis_count_after": _raw_count(user.pk)})


def test_d3_ten_in_flight_valid_attempts_are_neither_blocked_nor_counted(
    admin, farm_id, make_user, mailbox
):
    """D-3 corregido: 10 solicitudes simultáneas (3×200, 3×409, 4×404) en 10 procesos.

    Antes del cierre, 4 válidas recibían 429 y su unidad quedaba contada. Ahora
    una solicitud bloqueada solo por intentos en curso espera un hueco y el
    contador final es exactamente el de los fallos reales.
    """
    user = make_user("84340051", correo="d10-f1034b@example.com")
    code, code_member = _valid_and_member_codes(admin, farm_id, mailbox, user)
    _ready, results = _spawn(user.pk, _d_tasks(code, code_member, valid=3, member=3, wrong=4, net=37))
    per_kind = {"valid": [], "member": [], "wrong": []}
    for index, res in enumerate(results):
        kind = "valid" if index < 3 else "member" if index < 6 else "wrong"
        per_kind[kind].append(res["results"][0]["status"])
    counts = [r["results"][1]["count"] for r in results]
    assert per_kind == {"valid": [200] * 3, "member": [409] * 3, "wrong": [404] * 4}
    assert all(0 <= value <= MAX for value in counts)
    assert _raw_window(user.pk) == (4, 0)
    _evidence("D-3-fixed", {"per_kind": per_kind, "counts_seen_by_processes": counts,
                            "redis_window_after": _raw_window(user.pk)})


# --- E. TTL y expiración de la ventana ---------------------------------------------------

def test_e_ttl_is_set_once_and_kept_by_atomic_increments(make_user):
    user = make_user("84340060", correo="e1-f1034b@example.com")
    first = code_attempts.CodeAttemptGuard(user.pk)
    first.begin()
    first.failed()
    window_key, reservations_key = _key(user.pk, "window"), _key(user.pk, "reservations")
    ttl0 = _raw().ttl(window_key)
    assert code_attempts.WINDOW_SECONDS - 5 <= ttl0 <= code_attempts.WINDOW_SECONDS
    assert _raw().ttl(reservations_key) <= ttl0
    time.sleep(2.2)
    for _ in range(2):
        guard = code_attempts.CodeAttemptGuard(user.pk)
        guard.begin()
        guard.failed()
    ttl1 = _raw().ttl(window_key)
    assert _raw_window(user.pk) == (3, 0)
    assert ttl1 <= ttl0 - 2  # HINCRBY no reinicia el TTL de la ventana
    _evidence("E-ttl", {"window_seconds": code_attempts.WINDOW_SECONDS, "ttl_initial": ttl0,
                        "ttl_after_increments_2s_later": ttl1})


def test_e_window_expires_and_a_new_one_starts(make_user, monkeypatch):
    # TTL reducido SOLO en este proceso de pruebas; producción no cambia.
    monkeypatch.setattr(code_attempts, "WINDOW_SECONDS", 2)
    user = make_user("84340061", correo="e2-f1034b@example.com")
    outcomes = []
    for _ in range(MAX + 1):
        guard = code_attempts.CodeAttemptGuard(user.pk)
        try:
            guard.begin()
            guard.failed()
            outcomes.append("allowed")
        except Throttled:
            outcomes.append("blocked")
    assert outcomes == ["allowed"] * MAX + ["blocked"]
    window_key, reservations_key = _key(user.pk, "window"), _key(user.pk, "reservations")
    assert 0 < _raw().ttl(window_key) <= 2
    time.sleep(2.6)
    assert _raw().exists(window_key) == 0 and _raw().exists(reservations_key) == 0
    code_attempts.CodeAttemptGuard(user.pk).begin()  # ventana nueva: permitido
    assert _raw_window(user.pk) == (0, 1)
    assert 0 < _raw().ttl(window_key) <= 2
    _evidence("E-expiry", {"outcomes_first_window": outcomes, "after_expiry_count": 1})


# --- D-1. Reserva antigua frente a una ventana nueva (corregido en el cierre F10.3) ------

def test_d1_stale_resolution_does_not_touch_the_new_window(make_user, monkeypatch):
    """Misma secuencia que reprodujo D-1 en F10.3.4-B, con Redis y caducidad reales.

    1. Una petición «antigua» reserva su intento en la ventana W1.
    2. W1 caduca (TTL reducido solo en este proceso de pruebas).
    3. Otra petición falla en una ventana nueva W2 (1 fallo).
    4. La antigua resulta válida y llama a ``not_failed()``: W2 no cambia.
    """
    monkeypatch.setattr(code_attempts, "WINDOW_SECONDS", 2)
    user = make_user("84340070", correo="d1-f1034b@example.com")
    old = code_attempts.CodeAttemptGuard(user.pk)
    old.begin()
    time.sleep(2.6)
    assert _raw().exists(_key(user.pk, "window")) == 0  # W1 caducó
    monkeypatch.setattr(code_attempts, "WINDOW_SECONDS", 60)
    new = code_attempts.CodeAttemptGuard(user.pk)
    new.begin()
    new.failed()  # fallo en W2
    assert _raw_window(user.pk) == (1, 0)
    old.not_failed()  # la petición de W1 termina tarde
    after_release = _raw_window(user.pk)
    assert after_release == (1, 0)
    extra = []
    for _ in range(MAX + 1):
        guard = code_attempts.CodeAttemptGuard(user.pk)
        try:
            guard.begin()
            guard.failed()
            extra.append("allowed")
        except Throttled:
            extra.append("blocked")
    failures_evaluated_in_w2 = 1 + extra.count("allowed")
    assert failures_evaluated_in_w2 == MAX
    _evidence("D-1-fixed", {"w2_before_stale_release": [1, 0], "w2_after_stale_release": list(after_release),
                            "failures_evaluated_in_w2": failures_evaluated_in_w2, "limit": MAX})


def test_d1_resolution_is_once_and_never_negative_across_processes(admin, farm_id, make_user, mailbox):
    """Resoluciones repetidas, ajenas o tardías, desde procesos distintos, no bajan de 0."""
    user = make_user("84340071", correo="d1b-f1034b@example.com")
    guard = code_attempts.CodeAttemptGuard(user.pk)
    guard.begin()
    window, token = guard._window, guard._token
    guard.not_failed()
    for _ in range(3):
        code_attempts._resolve(user.pk, window, token, failed=False)
        code_attempts._resolve(user.pk, window, token, failed=True)
    code_attempts._resolve(user.pk, "ventana-ajena", token, failed=False)
    assert _raw_window(user.pk) == (0, 0)
    # Procesos independientes con fallos simultáneos: los contadores nunca son negativos.
    _ready, results = _spawn(user.pk, [[{"op": "count"}] for _ in range(4)])
    assert all(r["results"][0]["count"] == 0 for r in results)
    _evidence("D-1-once", {"window_after_repeated_resolutions": list(_raw_window(user.pk))})


# --- D-2. Throttles atómicos bajo concurrencia real (corregido en el cierre F10.3) ------

def _drf_blocked(results):
    """429 del throttle de DRF (espera ~1 hora) frente a los del guard (≤ 15 min)."""
    return sum(
        1 for res in results for r in res["results"]
        if r.get("status") == 429 and "hora" in r.get("error", "")
    )


def test_d2_account_throttle_admits_exactly_ten_from_concurrent_processes(
    admin, farm_id, make_user, mailbox
):
    """15 procesos simultáneos de la misma cuenta, cada uno con su IP.

    Antes del cierre pasaban entre 10 y 15. Ahora el throttle atómico admite
    exactamente 10; de ellas solo 5 se evalúan como fallos (límite del guard).
    """
    rate = django_settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["invitation_accept"]
    limit = int(rate.split("/")[0])
    assert rate == "10/hour"
    trials = []
    for trial in range(3):
        user = make_user(f"8434008{trial}", correo=f"d2-{trial}-f1034b@example.com")
        processes = limit + 5
        _ready, results = _spawn(
            user.pk,
            [[{"op": "post", "url": PREVIEW if i % 2 else ACCEPT, "code": WRONG, "ip": f"10.35.{trial}.{i}"}]
             for i in range(processes)],
        )
        statuses = _statuses(results)
        drf_blocked = _drf_blocked(results)
        history = _raw().zcard(cache.make_key(f"throttle_invitation_accept_{user.pk}:zset"))
        trials.append({"requests": processes, "passed_drf": processes - drf_blocked, "drf_history": history,
                       "status_404": statuses.count(404), "status_429": statuses.count(429)})
        assert processes - drf_blocked == limit
        assert history == limit
        assert statuses.count(404) == MAX
        assert _raw_window(user.pk) == (MAX, 0)
    _evidence("D-2-account-fixed", {"drf_rate": rate, "trials": trials})


def test_d2_ip_throttle_admits_exactly_thirty_from_concurrent_processes(make_user):
    """32 procesos simultáneos de cuentas distintas desde la misma IP.

    Cada cuenta hace una sola solicitud (no actúa la cuota por cuenta ni el
    guard). El throttle por IP atómico admite exactamente 30. Se envía además
    ``X-Forwarded-For`` variable, que no debe cambiar la identidad de la IP.
    """
    rate = django_settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["invitation_accept_ip"]
    limit = int(rate.split("/")[0])
    assert rate == "30/hour"
    users = [make_user(f"8437{index:04d}", correo=f"d2ip{index}-f1034b@example.com") for index in range(limit + 2)]
    _ready, results = _spawn(
        users[0].pk,
        [[{"op": "post", "url": PREVIEW, "code": WRONG, "ip": "203.0.113.90", "user_pk": user.pk}] for user in users],
    )
    statuses = _statuses(results)
    assert statuses.count(404) == limit
    assert statuses.count(429) == 2
    assert _drf_blocked(results) == 2
    _evidence("D-2-ip-fixed", {"drf_rate": rate, "requests": len(users), "status_404": statuses.count(404),
                               "status_429": statuses.count(429)})


# --- F. Redis en pausa (no responde) -----------------------------------------------------

def test_f_redis_paused_fails_closed(admin, farm_id, make_user, mailbox):
    """Pausa controlada del Redis desechable (CLIENT PAUSE) y respuesta real.

    Con Redis sin responder, django-redis agota su timeout (5 s); RedisCache
    vuelve a lanzar la excepción original de redis-py. Se observa el código
    HTTP real y que el código válido no se consume.
    """
    user = make_user("84340090", correo="f-f1034b@example.com")
    invitation, code = _invite(admin, farm_id, mailbox, user.correo)
    client = APIClient(REMOTE_ADDR="10.36.0.1")
    client.force_authenticate(user=user)
    client.raise_request_exception = False
    pause_ms = 18000
    started = time.monotonic()
    _raw().execute_command("CLIENT", "PAUSE", str(pause_ms), "ALL")
    try:
        preview = client.post(PREVIEW, {"code": code}, format="json")
        accept = client.post(ACCEPT, {"code": code}, format="json")
        guard_error = None
        try:
            code_attempts.CodeAttemptGuard(user.pk).begin()
        except Exception as exc:  # noqa: BLE001 - se registra el tipo
            # RedisCache vuelve a lanzar la causa original de ConnectionInterrupted.
            guard_error = f"{type(exc).__module__}.{type(exc).__name__}"
    finally:
        remaining = pause_ms / 1000 - (time.monotonic() - started) + 1
        if remaining > 0:
            time.sleep(remaining)
    assert preview.status_code == 500
    assert accept.status_code == 500
    assert guard_error == "redis.exceptions.TimeoutError"  # el guard no oculta el fallo
    invitation.refresh_from_db()
    assert invitation.status == InvitationStatus.PENDING
    assert not FarmMembership.objects.filter(farm_id=farm_id, user=user).exists()
    assert FarmInvitation.objects.filter(pk=invitation.pk, status=InvitationStatus.PENDING).exists()
    _raw().ping()  # Redis vuelve a responder
    _evidence("F-pause", {"preview_status": preview.status_code, "accept_status": accept.status_code,
                          "guard_error": guard_error, "invitation_status": invitation.status})
