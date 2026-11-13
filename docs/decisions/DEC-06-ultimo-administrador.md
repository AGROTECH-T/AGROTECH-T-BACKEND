# DEC-06 — Último administrador activo

- **Identificador:** DEC-06
- **Título:** Protección del último administrador activo
- **Estado de decisión:** Aprobada
- **Estado de implementación:** Implementada
- **Fecha:** 2026-11-07

## Contexto

Una finca sin administradores activos queda inoperable: nadie podría gestionar
integrantes, roles ni el estado de la finca. Las operaciones que retiran, degradan
o hacen abandonar a un administrador deben impedir dejar la finca sin
administración.

## Decisión

Cada finca debe conservar **al menos un `ADMIN` activo**.

- Antes de abandonar, revocar o degradar al último administrador, debe realizarse
  una **transferencia** de la administración.
- Se requiere **protección frente a concurrencia**: la comprobación y la escritura
  deben ejecutarse bajo bloqueo, de modo que dos operaciones simultáneas no puedan
  dejar la finca sin administrador.
- El orden de bloqueos es `Farm → Membership(s)` por PK.

## Consecuencias

- Degradar, revocar o abandonar al último administrador se rechaza con conflicto.
- La transferencia de administración es atómica.
- La aceptación de invitaciones (FARMS-108) también debe respetar esta regla.

## Trazabilidad al código

- Servicio: `apps/farms/services.py` — `_ensure_not_last_admin`,
  `change_member_role`, `revoke_member`, `leave_farm`, `transfer_admin`,
  `_admin_actor_for_membership_op` (bloqueos `Farm → Membership(s)` por PK).
- Auditoría: `apps/trazabilidad/models.py` — `EventType.ADMIN_TRANSFERRED`,
  `MEMBER_ROLE_CHANGED`, `MEMBER_REVOKED`, `MEMBER_LEFT`.
- Pruebas: `apps/farms/tests/test_membership_lifecycle.py`,
  `apps/farms/tests/test_membership_concurrency.py`.

## Brechas de implementación

- Ninguna brecha funcional para el alcance aprobado.
- Las pruebas de concurrencia dependen de una base MariaDB aislada; su ejecución
  no forma parte de esta etapa documental.
