# ruff: noqa: S101
"""Concurrencia en credenciales de gateway (IoT-03.1).

Propósito: emisiones y rotaciones simultáneas dejan una sola credencial
vigente y solo su secreto verifica; una rotación concurrente con la
desactivación del gateway no deja credenciales vigentes.
Contexto: ``transaction=True`` (cada hilo usa su conexión y confirma de verdad).
Solo mediante el orquestador desechable.
"""
import secrets
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.db import connections

from apps.accounts.errors import AppError
from apps.iot import services as iot
from apps.iot.models import GatewayCredential

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def key(settings):
    settings.GATEWAY_CREDENTIAL_HMAC_KEY = secrets.token_urlsafe(48)


def _parallel(*operations):
    barrier = Barrier(len(operations))

    def run(operation):
        try:
            barrier.wait(timeout=5)
            return operation()
        except AppError as exc:
            return exc
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=len(operations)) as pool:
        futures = [pool.submit(run, op) for op in operations]
        return [f.result(timeout=20) for f in futures]


def _active(gateway):
    return GatewayCredential.objects.filter(gateway=gateway, active_slot=1)


def test_parallel_issues_leave_one_credential(admin, farm_id):
    gateway = iot.create_gateway(user=admin, farm_id=farm_id, name="G")

    def issue():
        return iot.issue_gateway_credential(user=admin, farm_id=farm_id, gateway_uid=gateway.uid)

    results = _parallel(issue, issue)
    errors = [r for r in results if isinstance(r, AppError)]
    assert len(errors) == 1 and errors[0].status_code == 409
    assert _active(gateway).count() == 1


def test_parallel_rotations_leave_one_valid_secret(admin, farm_id):
    gateway = iot.create_gateway(user=admin, farm_id=farm_id, name="G")
    first = iot.issue_gateway_credential(user=admin, farm_id=farm_id, gateway_uid=gateway.uid)

    def rotate():
        return iot.rotate_gateway_credential(
            user=admin, farm_id=farm_id, gateway_uid=gateway.uid
        )

    results = _parallel(rotate, rotate)
    assert not any(isinstance(r, AppError) for r in results)  # se serializan por la finca
    assert _active(gateway).count() == 1
    assert GatewayCredential.objects.filter(gateway=gateway).count() == 3
    candidates = [first.secret] + [r.secret for r in results]
    valid = [s for s in candidates if iot.verify_gateway_credential(gateway.uid, s) is not None]
    assert len(valid) == 1


def test_rotation_racing_gateway_deactivation_leaves_no_active_credential(admin, farm_id):
    gateway = iot.create_gateway(user=admin, farm_id=farm_id, name="G")
    issued = iot.issue_gateway_credential(user=admin, farm_id=farm_id, gateway_uid=gateway.uid)

    results = _parallel(
        lambda: iot.rotate_gateway_credential(
            user=admin, farm_id=farm_id, gateway_uid=gateway.uid
        ),
        lambda: iot.deactivate_gateway(user=admin, farm_id=farm_id, uid=gateway.uid),
    )
    rotation = results[0]
    assert not isinstance(results[1], AppError)
    if isinstance(rotation, AppError):
        assert rotation.status_code == 409  # la desactivación ganó
    assert not _active(gateway).exists()
    for secret in [issued.secret] + ([] if isinstance(rotation, AppError) else [rotation.secret]):
        assert iot.verify_gateway_credential(gateway.uid, secret) is None
