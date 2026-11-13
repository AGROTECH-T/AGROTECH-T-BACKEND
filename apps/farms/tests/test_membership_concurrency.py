# ruff: noqa: S101
"""Pruebas de concurrencia del ciclo de vida de membresías."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.db import connections

from apps.accounts.errors import AppError
from apps.farms import services
from apps.farms.models import FarmMembership, FarmRole, MembershipStatus
from apps.trazabilidad.models import AuditEvent, EventType

pytestmark = pytest.mark.django_db(transaction=True)


def test_concurrent_leave_cannot_empty_admins(make_user):
    """Dos administradores abandonan a la vez; al menos uno debe quedarse."""
    admin_a = make_user("11111111")
    admin_b = make_user("22222222")
    membership = services.create_farm(user=admin_a, name="Concurrente")
    farm_id = membership.farm_id
    FarmMembership.objects.create(
        farm_id=farm_id,
        user=admin_b,
        role=FarmRole.ADMINISTRADOR,
    )

    def leave(user):
        try:
            return services.leave_farm(user=user, farm_id=farm_id)
        except AppError as exc:
            return exc
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(leave, admin_a), pool.submit(leave, admin_b)]
        results = [future.result(timeout=10) for future in futures]

    errors = [result for result in results if isinstance(result, AppError)]
    assert len(errors) >= 1

    remaining = FarmMembership.objects.filter(
        farm_id=farm_id,
        status=MembershipStatus.ACTIVE,
        role=FarmRole.ADMINISTRADOR,
    ).count()
    assert remaining >= 1


def test_concurrent_revoke_and_transfer_keep_admin(make_user):
    """Revocación y transferencia concurrentes no dejan la finca sin admin."""
    admin_a = make_user("11111111")
    admin_b = make_user("22222222")
    membership = services.create_farm(user=admin_a, name="Concurrente")
    farm_id = membership.farm_id
    admin_b_membership = FarmMembership.objects.create(
        farm_id=farm_id,
        user=admin_b,
        role=FarmRole.ADMINISTRADOR,
    )
    operator = make_user("33333333")
    target = FarmMembership.objects.create(
        farm_id=farm_id,
        user=operator,
        role=FarmRole.OPERADOR,
    )

    def revoke():
        try:
            return services.revoke_member(
                user=admin_a,
                farm_id=farm_id,
                membership_id=admin_b_membership.pk,
            )
        except AppError as exc:
            return exc
        finally:
            connections.close_all()

    def transfer():
        try:
            return services.transfer_admin(
                user=admin_b,
                farm_id=farm_id,
                membership_id=target.pk,
            )
        except AppError as exc:
            return exc
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(revoke), pool.submit(transfer)]
        for future in futures:
            future.result(timeout=10)

    active_admins = FarmMembership.objects.filter(
        farm_id=farm_id,
        status=MembershipStatus.ACTIVE,
        role=FarmRole.ADMINISTRADOR,
    ).count()
    assert active_admins >= 1


def test_concurrent_leave_with_inactive_admin_keeps_effective_admin(make_user):
    """Un admin con cuenta inactiva no cuenta como respaldo efectivo.

    El administrador efectivo intenta abandonar a la vez que el admin de cuenta
    inactiva; el efectivo debe ser bloqueado y la finca conserva al menos un
    administrador efectivo.
    """
    admin_a = make_user("11111111")
    admin_b = make_user("22222222", is_active=False)
    membership = services.create_farm(user=admin_a, name="Concurrente")
    farm_id = membership.farm_id
    FarmMembership.objects.create(
        farm_id=farm_id,
        user=admin_b,
        role=FarmRole.ADMINISTRADOR,
    )

    def leave(user):
        try:
            return services.leave_farm(user=user, farm_id=farm_id)
        except AppError as exc:
            return exc
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(leave, admin_a), pool.submit(leave, admin_b)]
        results = [future.result(timeout=10) for future in futures]

    errors = [result for result in results if isinstance(result, AppError)]
    assert len(errors) >= 1

    effective_admins = FarmMembership.objects.filter(
        farm_id=farm_id,
        status=MembershipStatus.ACTIVE,
        role=FarmRole.ADMINISTRADOR,
        user__is_active=True,
    ).count()
    assert effective_admins >= 1


def test_concurrent_transfers_keep_admin(make_user):
    """Dos administradores transfieren a la vez a dos operadores distintos."""
    admin_a = make_user("11111111")
    admin_b = make_user("22222222")
    operator_c = make_user("33333333")
    operator_d = make_user("44444444")

    admin_a_membership = services.create_farm(user=admin_a, name="Concurrente")
    farm_id = admin_a_membership.farm_id
    admin_b_membership = FarmMembership.objects.create(
        farm_id=farm_id, user=admin_b, role=FarmRole.ADMINISTRADOR
    )
    operator_c_membership = FarmMembership.objects.create(
        farm_id=farm_id, user=operator_c, role=FarmRole.OPERADOR
    )
    operator_d_membership = FarmMembership.objects.create(
        farm_id=farm_id, user=operator_d, role=FarmRole.OPERADOR
    )

    barrier = Barrier(2)

    def transfer(actor, membership_id):
        try:
            barrier.wait(timeout=5)
            return services.transfer_admin(
                user=actor, farm_id=farm_id, membership_id=membership_id
            )
        except AppError as exc:
            return exc
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(transfer, admin_a, operator_c_membership.pk),
            pool.submit(transfer, admin_b, operator_d_membership.pk),
        ]
        results = [future.result(timeout=10) for future in futures]

    errors = [result for result in results if isinstance(result, AppError)]
    assert errors == []

    admin_a_membership.refresh_from_db()
    admin_b_membership.refresh_from_db()
    operator_c_membership.refresh_from_db()
    operator_d_membership.refresh_from_db()

    assert admin_a_membership.role == FarmRole.OPERADOR
    assert admin_b_membership.role == FarmRole.OPERADOR
    assert operator_c_membership.role == FarmRole.ADMINISTRADOR
    assert operator_d_membership.role == FarmRole.ADMINISTRADOR

    effective_admins = FarmMembership.objects.filter(
        farm_id=farm_id,
        status=MembershipStatus.ACTIVE,
        role=FarmRole.ADMINISTRADOR,
        user__is_active=True,
    ).count()
    assert effective_admins == 2

    memberships = list(FarmMembership.objects.filter(farm_id=farm_id))
    assert len(memberships) == 4
    for user in (admin_a, admin_b, operator_c, operator_d):
        assert FarmMembership.objects.filter(farm_id=farm_id, user=user).count() == 1
    for membership in memberships:
        assert membership.status == MembershipStatus.ACTIVE
        assert membership.role in FarmRole.values

    assert (
        AuditEvent.objects.filter(
            farm_id=farm_id, event_type=EventType.ADMIN_TRANSFERRED
        ).count()
        == 2
    )
