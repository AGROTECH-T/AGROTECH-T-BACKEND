# DEC-03 — Invitaciones por correo

- **Identificador:** DEC-03
- **Título:** Invitaciones por correo
- **Estado de decisión:** Aprobada
- **Estado de implementación:** Implementada en backend; validación manual pendiente
- **Fecha:** 2026-11-07
- **Actualización B1:** 2026-11-11

## Contexto

Se necesita incorporar personas a una finca sin crear cuentas en su nombre ni
compartir credenciales. La invitación debe ser segura, de un solo uso y verificable,
y debe permitir invitar correos que aún no están registrados.

## Decisión

Las invitaciones se realizan por correo con las siguientes reglas:

- El **token es de un solo uso** y se almacena **mediante hash** (nunca en claro).
- La invitación tiene expiración, estado, destinatario, finca, rol e invitador.
- La **aceptación** requiere un usuario **registrado** cuyo **correo verificado**
  coincida con el destinatario de la invitación.
- Se permite invitar correos todavía no registrados; la persona debe registrarse y
  verificar ese mismo correo antes de aceptar.
- El diseño detallado (estados, límites, concurrencia, correo, auditoría y
  seguridad) se especifica en `docs/modules/farms-108-invitaciones.md`.

## Consecuencias

- Aceptar una invitación **no** concede permisos operativos automáticamente; la
  asignación de permisos es posterior y administrativa.
- Si la aceptación reactiva una membresía `REVOKED` o `LEFT`, esta comienza **sin
  permisos operativos**.
- La aceptación debe respetar DEC-06 (la finca conserva al menos un `ADMIN`
  activo).
- La implementación backend se completó en B1; la validación manual sigue pendiente.

## Trazabilidad al código

- Implementación backend: `apps/farms/models.py`,
  `apps/farms/invitation_services.py`, `apps/farms/views.py`,
  `apps/farms/urls.py` y `apps/farms/migrations/0004_farminvitation.py`.
- Origen de asignación previsto: `apps/permissions/models.py` —
  `AssignmentSource.INVITATION`.
- Permiso de presentación: `apps/farms/role_permisos_admin.py` —
  `gestionar_invitaciones` (solo presentación; no implica disponibilidad).

## Brechas de implementación

- El frontend de aceptación y la administración visual de invitaciones pendientes
  siguen pendientes.
- La validación manual y automatizada del backend está pendiente; no se declara
  comportamiento verificado en ejecución.
- La asignación posterior de permisos depende de DEC-05.
- La identificación del dominio y el diseño técnico detallado se mantienen en
  FARMS-108.

## Actualización A1 — Limpieza de permisos en membresías (2026-11-10)

> Registro complementario. No modifica las decisiones anteriores de este
> documento: concreta la consecuencia «la membresía reactivada comienza sin
> permisos operativos». La limpieza de permisos fue validada en F2-3; las
> invariantes restantes se cierran en B1.

### Decisiones registradas (A1-D1 a A1-D6, aprobadas en PLAN A1)

- Al salir (`leave_farm`), al ser revocada (`revoke_member`) o al reactivarse por
  aceptación de invitación (`accept_invitation`, solo al reutilizar una fila
  `LEFT` o `REVOKED`), la membresía pierde todas sus `PermissionAssignment` y
  `PermissionRestriction`. `ApprovalPolicy` no se modifica.
- Cada fila eliminada genera un evento individual: `permission_revoked` para
  asignaciones (`module`, `action` y `source` en `before`) y
  `permission_unrestricted` para restricciones (`module` y `action`). La causa se
  registra en `reason`: `member_left`, `member_revoked` o `member_reactivated`.
  No se crean `EventType` ni migraciones.
- La lógica se centraliza en `apps/permissions/services.py` —
  `clear_membership_permissions`. Farms la invoca con importación diferida desde
  `apps/farms/services.py` — `_clear_membership_permissions`.
- Configuración explícita `FARMS_PERMISSION_CLEANUP`: `required` en
  `config/settings.py` (heredado por las configuraciones completas) y `disabled`
  solo en `config/settings_test_disposable_isolated.py`. La ausencia equivale a
  `required`; `required` sin Permissions, `disabled` con Permissions o un valor
  desconocido producen `ImproperlyConfigured`.

### Estrategia transaccional

- La función no abre ni confirma transacción ni añade bloqueos. Se ejecuta dentro
  del `transaction.atomic()` del servicio llamador, después de bloquear la finca
  y la membresía y de completar las validaciones, incluida la protección del
  último administrador (DEC-06).
- Las filas de permisos se tratan después de los bloqueos existentes de cada
  operación. La serialización frente a `grant`, `restrict`, `ungrant` y
  `unrestrict` se obtiene porque todas bloquean primero la fila de `Farm`.
- Secuencia: selección por `membership_id`; auditoría por fila; DELETE exacto por
  `membership_id` y `pk__in` de los identificadores auditados, comparando el
  recuento del modelo objetivo (no el total que devuelve Django); DELETE residual
  por `membership_id`, que debe afectar a 0 filas.
- Cualquier discrepancia o fila residual lanza `RuntimeError`; el rollback del
  llamador revierte los DELETE, el cambio de estado y los eventos registrados.
- El orden de bloqueos en la aceptación es `Farm → Membership → Invitation`,
  confirmado en B1. A1 no depende de ese orden.

### Limitaciones

- REPEATABLE READ no se probó experimentalmente; la validación usó READ COMMITTED,
  predeterminado de Django en MariaDB y no modificado por `config/database.py`.
- Las escrituras que no usan los servicios (consola, admin, SQL directo) no
  quedan protegidas por el bloqueo de `Farm`.
- El DELETE residual detecta filas no auditadas y lanza una excepción para forzar
  el rollback; no garantiza que la operación se complete con éxito en todos los
  niveles de aislamiento (con REPEATABLE READ puede terminar en error).
- La condición de omisión del módulo de pruebas (C3) no se ejecutó con
  configuraciones reales rotas; se revisó estáticamente y los escenarios se
  probaron mediante simulación.
- DEC-05 permanece diferida a Etapa 2; DEC-05:123 no se actualiza en esta etapa.

Evidencia: FARMS-108 §9.1.

## Actualización B1 — Cierre de invariantes (2026-11-11)

- Orden de bloqueos confirmado: `Farm → Membership → Invitation`. La lectura
  inicial por token es solo una pista y no valida la invitación.
- La aceptación revalida invitación, finca, identidad y membresía después de
  adquirir los bloqueos.
- Límite confirmado: 50 invitaciones pendientes por finca; la solicitud número 51
  responde HTTP 409. La expiración perezosa se ejecuta antes del conteo.
- Entrega `REJECTED`: HTTP 502 controlado con `invitation_id`; la invitación
  permanece `PENDING` y `last_sent_at` no cambia.
- Entrega `INDETERMINATE`: respuesta exitosa con `delivery_pending=true`; la
  invitación permanece `PENDING`.
- Aceptación limitada a 10 solicitudes por hora por usuario y 30 por hora por IP.
  El throttle por IP usa `REMOTE_ADDR`; no confía en `X-Forwarded-For`.
- Comando periódico: `expire_farm_invitations`. El despliegue debe programarlo.
- Implementación backend realizada; validación manual y automatizada pendiente.
