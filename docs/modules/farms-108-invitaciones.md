# FARMS-108 — Diseño de invitaciones a fincas

- **Identificador:** FARMS-108
- **Título:** Diseño de invitaciones a fincas
- **Estado de decisión:** Aprobada
- **Estado de implementación:** Implementada en backend; validación manual pendiente
- **Fecha:** 2026-11-07
- **Actualización B1:** 2026-11-11

> Este documento registra el diseño aprobado y el estado de implementación
> backend al 2026-11-11. La validación manual y automatizada permanece pendiente;
> no se declara comportamiento verificado en ejecución.

> **Actualización A1 (2026-11-10):** la evidencia de implementación y validación
> de la limpieza de permisos en membresías está en §9.1.

## 1. Objetivo y alcance

Incorporar personas a una finca mediante invitaciones por correo, de forma segura,
de un solo uso y verificable, permitiendo invitar correos aún no registrados.

La decisión arquitectónica asociada es DEC-03. Este documento detalla sus reglas.

## 2. Estados

Estados de la invitación:

- `PENDING`
- `ACCEPTED`
- `CANCELLED`
- `EXPIRED`

## 3. Datos y restricciones

- `pending_email` puede ser nulo **únicamente** cuando la invitación ya no está
  pendiente.
- Se conserva el correo histórico normalizado en `email`.
- Restricción única compatible con MariaDB para `(farm, pending_email)`.
- La invitación registra destinatario, finca, rol e invitador.

## 4. Reglas de operación

- **Vigencia:** 7 días.
- Cualquier `ADMIN` activo puede **crear, cancelar, reenviar y cambiar el rol** de
  invitaciones pendientes.
- Se permite invitar correos **todavía no registrados**.
- Para aceptar, el usuario debe **registrarse y verificar el mismo correo**.
- El rol puede modificarse mientras la invitación esté pendiente.
- **Reenviar** invalida el token anterior y genera uno nuevo, conservando la misma
  fila de invitación.
- Una nueva invitación puede **reactivar** una membresía `REVOKED` o `LEFT`, sin
  duplicar la fila de membresía.

## 5. Token, enlace y seguridad

- El token es de **un solo uso** y se almacena **mediante hash**; nunca en claro.
- Enlace: `{FRONTEND_URL}/invitations/accept#token=...`.
- React debe enviar el token mediante **POST** y limpiar el fragmento con
  `history.replaceState`.
- Usar **HTTPS** en producción.
- **No** registrar tokens en logs ni exponerlos mediante encabezados `Referer`.

## 6. Concurrencia y aceptación

- La **creación** se realiza bajo bloqueo de la finca.
- Si existe una invitación anterior **expirada** para el mismo correo, debe expirar
  y liberar `pending_email`.
- Si existe una invitación **vigente**, responder con **conflicto HTTP 409**.
- La **aceptación** usa una lectura inicial sin bloqueo únicamente como pista.
  Después adquiere los bloqueos y revalida hash, estado, expiración, identidad,
  finca activa y estado de la membresía antes de escribir.
- Orden de bloqueos: `Farm → Membership → Invitation`.
- Secuencia: lectura por token sin bloqueo; bloqueo de `Farm`; bloqueo de
  `Membership` si existe; bloqueo de `Invitation`; revalidación completa;
  aceptación, reactivación o creación de membresía.
- La lectura inicial no se considera validación definitiva.

## 7. Correo

- El envío ocurre **fuera de la transacción**.
- `ACCEPTED`: se confirma la entrega y se actualiza `last_sent_at`.
- `REJECTED`: la invitación permanece `PENDING`; la API responde **HTTP 502** con
  `error` e `invitation_id`. `last_sent_at` no cambia y el reenvío puede intentarse
  nuevamente.
- `INDETERMINATE`: la invitación permanece `PENDING`; la API responde con éxito y
  `delivery_pending=true`. No se declara que el correo fue entregado.
- Las respuestas de invitación exponen `delivery_pending`.
- **No** implementar patrón outbox en esta primera versión.

## 8. Expiración y límites

- Se considera expirada cuando `expires_at <= now`.
- Expiración **perezosa** durante aceptación, creación y listado, más el comando
  `expire_farm_invitations` para mantenimiento periódico.
- El repositorio no incluye scheduler; el despliegue debe invocar el comando
  periódicamente.
- Límites:
  - Crear: **30 solicitudes por hora por `ADMIN`**.
  - Reenviar y cambiar rol: **10 por hora por `ADMIN`**.
  - Aceptar: **10 por hora por usuario** y **30 por hora por IP**.
- El throttle por IP usa `REMOTE_ADDR` y deriva la clave con SHA-256. No confía en
  `X-Forwarded-For`. Detrás de un proxy inverso sin configuración confiable, varios
  clientes pueden compartir la IP observada.
- Máximo: **50 invitaciones pendientes por finca**. La solicitud número 51 responde
  **HTTP 409**. La expiración perezosa se ejecuta antes del conteo; el conteo y la
  creación quedan bajo el bloqueo de `Farm`.

## 9. Permisos operativos al aceptar

- Aceptar una invitación **no concede permisos operativos automáticamente**.
- Si la invitación reactiva una membresía `REVOKED` o `LEFT`, esta comienza **sin
  permisos operativos**.
- Un `ADMIN` activo debe asignar los permisos necesarios **posteriormente**.
- La aceptación debe respetar la regla de **DEC-06**: ninguna operación puede dejar
  la finca sin al menos un `ADMIN` activo.

### 9.1 Implementación y validación A1 (2026-11-10)

> Registro de evidencia. No modifica las reglas anteriores de esta sección. La
> decisión complementaria está en DEC-03, «Actualización A1».

**Comportamiento.** Al salir, al ser revocada o al reactivarse por invitación
(solo al reutilizar una fila `LEFT`/`REVOKED`), la membresía pierde todas sus
asignaciones y restricciones antes del cambio de estado, dentro de la misma
transacción. Una membresía nueva no ejecuta limpieza ni genera eventos de
permisos. Si la aceptación falla una validación (por ejemplo, correo no
verificado o membresía ya activa), no hay limpieza ni auditoría de limpieza.

**Auditoría.** Un evento por fila (`permission_revoked` /
`permission_unrestricted`) con la causa en `reason`. Los eventos históricos no
se modifican.

**Rollback.** Los fallos de borrado, de auditoría o de configuración revierten
la operación completa: estado de la membresía, filas de permisos, invitación y
eventos.

**Concurrencia.**
- T10 (`test_concurrent_accept_reactivation_cleans_once`): la segunda aceptación
  intenta el bloqueo de `Farm` mientras la primera lo retiene y no lo adquiere
  hasta que la primera confirma; después responde 404 con
  `INVITATION_NOT_FOUND`, la invitación queda `ACCEPTED` y la limpieza se
  registra una sola vez. Solo se instrumenta el bloqueo de `Farm`.
- T10b (`test_concurrent_grant_and_exit_leave_no_assignment`, casos `LEFT` y
  `REVOKED`): una concesión en curso retiene el bloqueo de `Farm`; la salida o
  revocación intenta adquirirlo, espera y, tras la confirmación, elimina y audita
  la asignación concedida.

**Configuración y C3.** Se probaron, mediante simulación, los cuatro escenarios de
`FARMS_PERMISSION_CLEANUP`, la opción ausente y los valores inválidos. El módulo
de pruebas se omite solo con `disabled` y sin Permissions (configuración aislada).

**Resultados de VALIDACIÓN V1–V6** (MariaDB 11.8.8 desechable, 127.0.0.1:3317).
Esta evidencia es histórica y corresponde a A1; no valida las invariantes de B1:

| Ejecución | Configuración | Selección | Resultado |
|---|---|---|---|
| V3/V4 (RunId `33195bb18c30`) | `config.settings_test_disposable` | `test_membership_permission_cleanup.py` | 34 aprobadas |
| Regresión (RunId `c9f9b4509ba2`) | `config.settings_test_disposable` | `apps/farms/tests`, `apps/trazabilidad/tests`, `apps/permissions/tests` | 341 aprobadas (incluye las 34 anteriores) |
| F2-2 aislada (RunId `78aa06d1fd97`) | `config.settings_test_disposable_isolated` | Lista F2-2 + archivo A1 | 131 aprobadas, 1 módulo omitido (C3); `validate_isolated_*` aprobadas |
| V5 | — | Ruff `check --no-fix` sobre los 6 archivos de A1 | All checks passed |

Las ejecuciones se solapan: no representan 506 pruebas únicas. V1–V6 se
completaron sin fallos reportados.

**Limitaciones.** REPEATABLE READ no se probó experimentalmente. Las escrituras
externas a los servicios no quedan protegidas por el bloqueo de `Farm`. El DELETE
residual fuerza rollback ante filas no auditadas, pero no garantiza
disponibilidad en todos los niveles de aislamiento. C3 con configuraciones
reales rotas no se ejecutó (revisión estática y simulación).

## 10. Auditoría

Eventos a registrar:

- `invitation_created`
- `invitation_resend_started`
- `invitation_resent`
- `invitation_role_changed`
- `invitation_delivery_failed`
- `invitation_cancelled`
- `invitation_expired`
- `invitation_accepted`
- `member_joined`
- `member_reactivated`

## 11. Configuración

- `FRONTEND_URL` depende del entorno.
- El dominio definitivo de producción está **pendiente de definición**.

## 12. Trazabilidad y estado

- Decisión asociada: `docs/decisions/DEC-03-invitaciones-por-correo.md`.
- Implementación backend: `apps/farms/models.py`,
  `apps/farms/invitation_services.py`, `apps/farms/views.py`,
  `apps/farms/urls.py` y `apps/farms/migrations/0004_farminvitation.py`.
- Origen de asignación previsto: `apps/permissions/models.py` —
  `AssignmentSource.INVITATION`.
- Permiso de presentación: `apps/farms/role_permisos_admin.py` —
  `gestionar_invitaciones` (solo presentación).

## 13. Brechas de implementación

- El frontend de aceptación y la administración visual de invitaciones
  pendientes (reenvío, cambio de rol y cancelación, F9) están implementados con
  pruebas automatizadas sin DOM; falta la validación manual en navegador.
- Reenvío, cambio de rol, cancelación, rotación del token y ausencia de tokens en
  respuestas, eventos y registros tienen pruebas automatizadas en el entorno
  desechable (`apps/farms/tests/test_invitation_admin_f9.py`). No hay validación
  contra un proveedor de correo real.
- La asignación posterior de permisos depende de la Unidad 7 (DEC-05).
- El dominio definitivo de producción de `FRONTEND_URL` no está definido.
- El despliegue debe programar la ejecución periódica de
  `expire_farm_invitations` y definir la configuración confiable de IP detrás de
  proxies inversos.
