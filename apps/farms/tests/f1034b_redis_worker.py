# ruff: noqa: S101
"""Proceso independiente para validar el contador de intentos con Redis real (F10.3.4-B).

Propósito: ejecutar, en un proceso del sistema operativo distinto del de
pytest, solicitudes reales a ``/api/v1/invitations/code/preview/`` y
``/code/accept/`` (cliente de pruebas de DRF, sin red) u operaciones directas
de ``CodeAttemptGuard`` contra el MISMO Redis desechable.

Contexto: lo lanza ``test_code_attempts_redis_f1034b.py`` con
``python -m apps.farms.tests.f1034b_redis_worker`` y hereda el entorno que el
orquestador da a pytest (configuración desechable, RunId y credenciales
temporales). Antes de cualquier operación demuestra la identidad de la MariaDB
desechable y del Redis desechable, e instala el bloqueo de correo y HTTP. Las
tareas llegan en ``F1034B_TASKS`` (JSON). Protocolo por stdout: una línea
``READY {...}``; espera ``GO`` por stdin para que todos los procesos salgan a
la vez; termina con una línea ``RESULT {...}``. Nunca imprime secretos.

No es un módulo de pruebas (no empieza por ``test_``).
"""
import json
import os
import sys


def _emit(tag: str, payload: dict) -> None:
    sys.stdout.write(f"{tag} {json.dumps(payload)}\n")
    sys.stdout.flush()


def _setup() -> dict:
    from config import disposable_preflight as pf

    pf.install_mail_guards()
    import django

    django.setup()
    from django.conf import settings
    from django.db import connection
    from django.test.utils import setup_test_environment

    # Igual que pytest-django: correo en memoria y host «testserver» permitido.
    setup_test_environment()
    assert settings.EMAIL_BACKEND == "django.core.mail.backends.locmem.EmailBackend"

    import config.settings_test_disposable as guard

    assert settings.SETTINGS_MODULE in pf.ALLOWED_SETTINGS_MODULES
    assert guard.disposable_redis_enabled(), "el trabajador exige el Redis desechable"
    guard.validate_live_disposable_database(connection)
    guard.validate_disposable_cache(
        settings.CACHES, getattr(settings, "DJANGO_REDIS_IGNORE_EXCEPTIONS", False)
    )
    guard.validate_live_disposable_redis()
    settings.INVITATION_CODE_HMAC_KEY = os.environ["F1034B_HMAC_KEY"]
    return {
        "pid": os.getpid(),
        "cache_location": settings.CACHES["default"]["LOCATION"],
        "database": connection.settings_dict["NAME"],
    }


def _run(tasks: list) -> list:
    from rest_framework.exceptions import Throttled
    from rest_framework.test import APIClient

    from apps.accounts.models import User
    from apps.farms import code_attempts

    default_user = User.objects.get(pk=int(os.environ["F1034B_USER_PK"]))
    results = []
    for task in tasks:
        op = task["op"]
        user = default_user
        if "user_pk" in task:
            user = User.objects.get(pk=int(task["user_pk"]))
        if op == "post":
            client = APIClient(REMOTE_ADDR=task.get("ip", "10.20.0.1"))
            client.force_authenticate(user=user)
            client.raise_request_exception = False
            response = client.post(task["url"], {"code": task["code"]}, format="json")
            item = {"op": op, "url": task["url"], "status": response.status_code}
            if response.status_code == 429:
                # Solo el texto genérico del límite (sin datos de la cuenta).
                item["error"] = str(getattr(response, "data", {}).get("error", ""))
            results.append(item)
        elif op == "begin":
            guard = code_attempts.CodeAttemptGuard(user.pk)
            try:
                guard.begin()
                results.append({"op": op, "result": "allowed"})
            except Throttled:
                results.append({"op": op, "result": "blocked"})
        elif op == "count":
            results.append({"op": op, "count": code_attempts.failed_attempts(user.pk)})
        else:  # pragma: no cover - tarea desconocida
            raise ValueError(op)
    return results


def main() -> int:
    tasks = json.loads(os.environ["F1034B_TASKS"])
    info = _setup()
    _emit("READY", info)
    line = sys.stdin.readline().strip()
    if line != "GO":
        _emit("RESULT", {"pid": info["pid"], "error": "sin señal GO"})
        return 2
    try:
        results = _run(tasks)
    except Exception as exc:  # noqa: BLE001 - se informa el tipo, nunca el detalle
        _emit("RESULT", {"pid": info["pid"], "error": type(exc).__name__})
        return 1
    finally:
        from django.db import connections

        connections.close_all()
    _emit("RESULT", {"pid": info["pid"], "results": results})
    return 0


if __name__ == "__main__":
    sys.exit(main())
