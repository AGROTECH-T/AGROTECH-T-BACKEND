# ruff: noqa: S101, S105
"""Concurrencia en credenciales de coordinador (IoT-03.2).

Propósito: emisiones y rotaciones simultáneas dejan una sola credencial vigente
y solo su clave autentica tramas; una rotación concurrente con la desactivación
del coordinador no deja credenciales vigentes.
Contexto: ``transaction=True`` (cada hilo usa su conexión y confirma de verdad).
Solo mediante el orquestador desechable.
"""
import base64
import secrets
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.db import connections, transaction

from apps.accounts.errors import AppError
from apps.iot import coordinator_credentials as crypto
from apps.iot import services as iot
from apps.iot.models import (
    CoordinatorBootWindow,
    CoordinatorCredential,
    CoordinatorKeyGeneration,
)
from apps.iot.services.coordinator_credentials import AuthenticatedCoordinatorFrame
from apps.iot.tests import lora_vectors
from config.coordinator_key_encryption_key import load_master_key_ring

pytestmark = pytest.mark.django_db(transaction=True)

CONTEXT = b"contexto-de-prueba"


@pytest.fixture(autouse=True)
def master_key(settings):
    settings.COORDINATOR_KEY_ENCRYPTION_KEY = base64.b64encode(
        secrets.token_bytes(32)
    ).decode("ascii")
    settings.COORDINATOR_KEY_ENCRYPTION_KEY_VERSION = "1"
    settings.COORDINATOR_KEY_ENCRYPTION_KEY_PREVIOUS = ""


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


def _active(coordinator):
    return CoordinatorCredential.objects.filter(coordinator=coordinator, active_slot=1)


def _stored_key(credential):
    return crypto.decrypt_coordinator_key(
        credential.ciphertext,
        credential.nonce,
        coordinator_uid=credential.coordinator.uid,
        key_ring=load_master_key_ring(),
        master_key_version=credential.master_key_version,
    )


def _verifies(credential, key) -> bool:
    # Trama v1 real y coherente con los campos (M-02, IoT-04.2).
    signed = lora_vectors.signed_bytes(
        uid=credential.coordinator.uid,
        key_version=credential.key_version,
        boot_counter=1,
        seq=1,
    )
    frame = crypto.ParsedCoordinatorFrame(
        coordinator_uid=credential.coordinator.uid,
        key_version=credential.key_version,
        boot_counter=1,
        seq=1,
        signed_bytes=signed,
        tag=crypto.compute_lora_hmac(key, signed, context=CONTEXT),
    )
    return iot.verify_coordinator_frame(frame=frame, hmac_context=CONTEXT) is not None


def test_parallel_issues_leave_one_credential(admin, farm_id):
    coordinator = iot.create_coordinator(user=admin, farm_id=farm_id, name="C")

    def issue():
        return iot.issue_coordinator_credential(
            user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid
        )

    results = _parallel(issue, issue)
    errors = [result for result in results if isinstance(result, AppError)]
    assert len(errors) == 1 and errors[0].status_code == 409
    assert _active(coordinator).count() == 1
    assert CoordinatorCredential.objects.filter(coordinator=coordinator).count() == 1
    active = _active(coordinator).get()
    assert active.key_version == 1
    assert CoordinatorKeyGeneration.objects.get(coordinator=coordinator).last_key_version == 1


def test_parallel_rotations_leave_one_active_and_only_its_key_verifies(admin, farm_id):
    coordinator = iot.create_coordinator(user=admin, farm_id=farm_id, name="C")
    iot.issue_coordinator_credential(
        user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid
    )

    def rotate():
        return iot.rotate_coordinator_credential(
            user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid
        )

    results = _parallel(rotate, rotate)
    assert not [result for result in results if isinstance(result, AppError)]
    assert _active(coordinator).count() == 1
    active = _active(coordinator).get()
    stored = _stored_key(active)
    assert [result.key == stored for result in results].count(True) == 1
    assert _verifies(active, stored)
    assert active.key_version == 3
    assert sorted(
        CoordinatorCredential.objects.filter(coordinator=coordinator).values_list(
            "key_version", flat=True
        )
    ) == [1, 2, 3]
    assert CoordinatorKeyGeneration.objects.get(coordinator=coordinator).last_key_version == 3
    assert CoordinatorCredential.objects.filter(coordinator=coordinator).count() == 3


def test_rotation_racing_deactivation_leaves_no_active_credential(admin, farm_id):
    coordinator = iot.create_coordinator(user=admin, farm_id=farm_id, name="C")
    iot.issue_coordinator_credential(
        user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid
    )

    def rotate():
        return iot.rotate_coordinator_credential(
            user=admin, farm_id=farm_id, coordinator_uid=coordinator.uid
        )

    def deactivate():
        return iot.deactivate_coordinator(
            user=admin, farm_id=farm_id, uid=coordinator.uid, reason="concurrente"
        )

    _parallel(rotate, deactivate)
    assert not _active(coordinator).exists()


def test_parallel_boot_acceptance_creates_one_row(admin, farm_id):
    coordinator = iot.create_coordinator(user=admin, farm_id=farm_id, name="C")
    frame = AuthenticatedCoordinatorFrame(
        coordinator_id=coordinator.pk,
        coordinator_uid=coordinator.uid,
        farm_id=farm_id,
        credential_id=1,
        boot_counter=5,
        seq=1,
        key_version=1,
    )

    def accept():
        with transaction.atomic():
            iot.accept_coordinator_sequence(frame=frame)

    _parallel(accept, accept)
    rows = list(
        CoordinatorBootWindow.objects.filter(coordinator=coordinator).values_list(
            "boot_counter", flat=True
        )
    )
    assert rows == [5]