# ruff: noqa: S101, S105, S106
"""F10.3.4-B (F): preview y accept con el Redis desechable DETENIDO.

Propósito: observar la respuesta HTTP real y el estado de la invitación cuando
Redis deja de existir (conexión rechazada), sin cambiar ninguna política.
Contexto: detiene el Redis desechable de la ejecución con ``SHUTDOWN NOSAVE``
(sin datos persistentes que perder). Debe ejecutarse SOLO, en una invocación
propia de ``-Validate -WithRedis``, porque después Redis ya no está
disponible. Con LocMemCache se omite. Datos sintéticos y clave HMAC ficticia.
"""
import json
import os
import re
import time
from pathlib import Path

import pytest
from django.conf import settings as django_settings
from rest_framework.test import APIClient

from apps.accounts.integrations.delivery import DeliveryOutcome
from apps.farms import code_attempts, invitation_services, services
from apps.farms.models import FarmMembership, FarmRole, InvitationStatus

REDIS_ON = django_settings.CACHES.get("default", {}).get("BACKEND") == "django_redis.cache.RedisCache"

pytestmark = [
    pytest.mark.skipif(not REDIS_ON, reason="requiere el Redis desechable real (-Validate -WithRedis)"),
    pytest.mark.django_db(transaction=True),
]

_GROUP = "[ABCDEFGHJKMNPQRSTUVWXYZ23456789]{5}"
CODE_RE = re.compile(rf"\b{_GROUP}-{_GROUP}\b")


def test_f_redis_down_preview_and_accept_fail_closed(make_user, monkeypatch):
    import redis
    from django_redis.cache import RedisCache

    import config.settings_test_disposable as guard

    sent = []

    def deliver(*, to, subject, text, html, timeout=15):
        sent.append(CODE_RE.findall(text)[0])
        return DeliveryOutcome.ACCEPTED

    monkeypatch.setattr(invitation_services, "deliver_email", deliver)
    admin = make_user("84340100", correo="admin-down-f1034b@example.com")
    user = make_user("84340101", correo="down-f1034b@example.com")
    farm_id = services.create_farm(user=admin, name="Finca F1034B caída").farm_id
    invitation = invitation_services.create_invitation(
        user=admin, farm_id=farm_id, email=user.correo, role=FarmRole.OPERADOR
    )
    code = sent[-1]

    raw = redis.Redis(
        host=guard.DEFAULT_TEST_REDIS_HOST,
        port=int(guard.DEFAULT_TEST_REDIS_PORT),
        password=os.environ[guard.DISPOSABLE_REDIS_CREDENTIAL_ENV],
        socket_timeout=5,
    )
    try:
        raw.shutdown(nosave=True)
    except redis.exceptions.ConnectionError:
        pass  # el servidor cierra la conexión al detenerse
    time.sleep(1)
    with pytest.raises(redis.exceptions.ConnectionError):
        raw.ping()

    # El teardown de conftest llama a cache.clear(): sin Redis fallaría. Solo
    # para este proceso de pruebas se neutraliza clear() (no afecta al guard).
    monkeypatch.setattr(RedisCache, "clear", lambda self: None)

    client = APIClient(REMOTE_ADDR="10.38.0.1")
    client.force_authenticate(user=user)
    client.raise_request_exception = False
    started = time.monotonic()
    preview = client.post("/api/v1/invitations/code/preview/", {"code": code}, format="json")
    accept = client.post("/api/v1/invitations/code/accept/", {"code": code}, format="json")
    elapsed = round(time.monotonic() - started, 2)
    guard_error = None
    try:
        code_attempts.CodeAttemptGuard(user.pk).begin()
    except Exception as exc:  # noqa: BLE001 - se registra el tipo real
        guard_error = f"{type(exc).__module__}.{type(exc).__name__}"

    assert preview.status_code == 500
    assert accept.status_code == 500
    assert guard_error == "redis.exceptions.ConnectionError"
    invitation.refresh_from_db()
    assert invitation.status == InvitationStatus.PENDING
    assert not FarmMembership.objects.filter(farm_id=farm_id, user=user).exists()

    report_dir = os.environ.get("AGROTECH_DISPOSABLE_REPORT_DIR")
    if report_dir:
        with open(Path(report_dir) / "f1034b-evidence.jsonl", "a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "test": "F-down", "preview_status": preview.status_code,
                "accept_status": accept.status_code, "guard_error": guard_error,
                "elapsed_seconds_two_requests": elapsed, "invitation_status": invitation.status,
            }) + "\n")
