# API de fincas

Base local: `http://localhost:8080/api/v1`

Todas las rutas requieren la autenticación Bearer existente:

```http
Authorization: Bearer <token>
```

Los errores mantienen el contrato general del backend: `{"error": "mensaje"}`.

En `POST /farms/` el único campo permitido es `name`. Cualquier campo adicional,
incluidos `role`, `user` o un campo desconocido, se rechaza con `400`.

## Roles de membresía

El rol pertenece a la relación entre una cuenta y una finca; no es un rol global
de la cuenta.

| Valor interno | Nombre funcional |
|---|---|
| `administrador` | `ADMINISTRADOR` |
| `operador` | `OPERADOR` |
| `consulta` | `CONSULTA` |

La API devuelve el valor interno. El creador de una finca recibe automáticamente
`administrador` en esa finca.

Los roles ya se aplican en la edición de fincas y en la escritura de inventario:
`administrador` y `operador` pueden escribir; `consulta` mantiene acceso de solo
lectura.

## Campos públicos de una finca

| Campo | Descripción |
|---|---|
| `id` | Identificador de la finca |
| `name` | Nombre de la finca; máximo 100 caracteres y puede repetirse |
| `role` | Rol de la cuenta autenticada en esa finca |
| `created_at` | Fecha de creación |
| `updated_at` | Fecha de actualización |
| `is_active` | Si la finca está activa |
| `deactivated_at` | Fecha de desactivación o `null` |
| `capabilities` | Capacidades de la cuenta en la finca, calculadas por el servidor (DEC-11) |

### Capacidades por finca (`capabilities`)

Objeto de solo lectura con **ocho** booleanos obligatorios. Son informativos para
la interfaz: cada endpoint sigue aplicando su propia autorización.

| Clave | Verdadera cuando |
|---|---|
| `can_edit_farm` | Administrador activo y finca activa |
| `can_deactivate_farm` | Administrador activo y finca activa |
| `can_reactivate_farm` | Administrador activo, finca desactivada y cuenta activa |
| `can_view_history` | Administrador activo (finca activa o desactivada) |
| `can_manage_members` | Administrador activo, finca activa y cuenta activa |
| `can_manage_invitations` | Administrador activo, finca activa y cuenta activa |
| `can_read_inventory` | Membresía activa y finca activa |
| `can_write_inventory` | Lo anterior y rol `administrador` u `operador` |

No existe `can_leave_farm`, por decisión (DEC-11): abandonar depende de no ser el
último administrador, regla que solo comprueba `POST /farms/{id}/leave/`, que puede
responder `409`.

## Listar fincas propias

`GET /farms/`

Devuelve únicamente las fincas activas donde la cuenta autenticada tiene
membresía. Las fincas desactivadas no aparecen en este listado operativo.

```json
[
  {
    "id": 1,
    "name": "La Esperanza",
    "role": "administrador",
    "created_at": "2026-10-23T06:48:00-05:00",
    "updated_at": "2026-10-23T06:48:00-05:00"
  }
]
```

- `200`: listado de fincas propias
- `401`: solicitud sin sesión válida

## Crear una finca

`POST /farms/`

```json
{
  "name": "La Esperanza"
}
```

El cliente solo puede enviar `name`. Los campos `role`, `user`, `user_id` o
cualquier otro campo desconocido se rechazan con `400`. El usuario creador se
obtiene de la sesión y la membresía inicial siempre es `administrador`. La finca
y la membresía se crean en una sola transacción.

Requiere cuenta activa y correo verificado; sin verificar responde
`403 {"error": "verifica tu correo para crear fincas"}`, aunque la cuenta tenga la
capacidad.

Además requiere la capacidad global `can_create_farms` (DEC-11) o la excepción de
primera finca (F10.2), ambas visibles en `GET /auth/me/` (`can_create_farms`,
`can_create_first_farm`). Sin ninguna responde `403 {"error": "tu cuenta no tiene
permiso para crear fincas"}`; ser administrador de otra finca no la concede.

Primera finca: si la cuenta no tiene la capacidad y nunca tuvo una membresía de
administrador, nunca creó una finca y nunca recibió ni perdió la capacidad, la
creación se permite y, en la misma transacción, concede `can_create_farms=True` y
registra `farm_creation_granted` con `actor` = la cuenta, motivo "primera finca
creada desde el onboarding" y `metadata = {"execution_source":
"first_farm_onboarding", "farm_id": <id>}`. Si cualquier paso falla no queda
finca, membresía, capacidad ni evento. La excepción no se puede reutilizar: tras
un `revoke_farm_creation` la cuenta ya no crea fincas. Nunca se concede
`is_staff` ni `is_superuser` (el modelo de cuenta no los tiene).

Reintentos de la primera finca (F10.2-CORRECCIONES): la cuenta se bloquea al
inicio, así que sus solicitudes se procesan de una en una. Si una solicitud
repite la operación de onboarding —mismo nombre (tras recortar), dentro de los
10 minutos siguientes a la concesión por primera finca, con esa finca activa y
sin otras fincas administradas— responde `201` con la **misma** finca, sin crear
otra ni registrar eventos. Cubre el doble envío simultáneo y el reintento tras
perder la respuesta. No se deduplican:
- creaciones con otro nombre, fuera de la ventana o después de crear otra finca
  (son creaciones adicionales legítimas);
- creaciones de cuentas que ya tenían `can_create_farms` (no hay operación de
  onboarding que reconocer). Para ellas haría falta una clave de idempotencia
  enviada por el cliente: cambio de contrato pendiente de autorización.

### Validación del nombre

- Es obligatorio.
- Se rechaza si está vacío o compuesto únicamente por espacios.
- El máximo es de 100 caracteres; un nombre de exactamente 100 es válido.
- Los espacios internos se conservan; no se colapsan.

- `201`: finca creada
- `400`: nombre inválido o campo no permitido
- `401`: solicitud sin sesión válida
- `403`: correo sin verificar, o sin capacidad ni excepción de primera finca

Respuesta `201`:

```json
{
  "id": 1,
  "name": "La Esperanza",
  "role": "administrador",
  "created_at": "2026-10-23T06:48:00-05:00",
  "updated_at": "2026-10-23T06:48:00-05:00"
}
```

## Consultar una finca

`GET /farms/{id}/`

- `200`: la finca está activa y la cuenta pertenece a ella
- `200`: la finca está desactivada y la cuenta es administrador actual
- `401`: solicitud sin sesión válida
- `404`: la finca no existe, la cuenta no pertenece a ella o está desactivada y
  la cuenta no es administrador actual

El `404` se usa también para fincas ajenas, con el mismo cuerpo de error que para
una finca inexistente, de modo que no se revela su existencia.

## Desactivar una finca

`DELETE /farms/{id}/`

La operación aplica una baja lógica. No elimina la finca, sus membresías, sus
productos ni sus movimientos. Registra la fecha y la cuenta que la desactivó.
Solo un administrador actual puede ejecutarla y es idempotente: repetirla no
cambia la fecha ni el responsable original. La edición de la finca y las
escrituras de Inventario bloquean la fila de la finca y revalidan su estado
activo antes de confirmar.

- `204`: finca desactivada o ya desactivada
- `401`: solicitud sin sesión válida
- `403`: la cuenta no es administrador de la finca
- `404`: la finca no existe o la cuenta no pertenece a ella

## Historial de la finca

`GET /farms/{id}/history/`

Devuelve el historial de auditoría paginado de la finca. Solo un administrador
actual puede consultarlo. Permite consultar fincas activas y desactivadas; la
consulta es de solo lectura y **no reactiva** la finca.

- `200`: historial paginado
- `401`: solicitud sin sesión válida
- `404`: la finca no existe o la cuenta no es administrador

Parámetros de paginación: `page` y `page_size` (máximo 100; por defecto 20).

## Historial de una membresía

`GET /farms/{id}/members/{membership_id}/history/`

Devuelve el historial de auditoría paginado de una membresía. Solo un
administrador actual puede consultarlo.

- `200`: historial paginado
- `401`: solicitud sin sesión válida
- `403`: la cuenta no es administrador
- `404`: la finca o la membresía no existen

## Invitaciones

Las invitaciones son de un solo uso, tienen vigencia de 7 días y requieren que la
cuenta autenticada tenga el mismo correo verificado que el destinatario. Aceptar
una invitación no concede permisos operativos automáticamente.

Estados: `pending`, `accepted`, `cancelled`, `expired`.

Solo una invitación `pending` admite reenvío, cambio de rol o cancelación; en
cualquier otro estado esas operaciones responden `409`. Todas las operaciones de
administración exigen administrador activo en finca activa; en la interfaz se
ofrecen solo con la capacidad `can_manage_invitations` confirmada.

Reglas del token:

- El token nunca se devuelve en una respuesta ni se registra en la auditoría; se
  guarda solo su huella SHA-256.
- Reenviar y cambiar el rol generan un token nuevo y renuevan la vigencia a 7
  días: el enlace anterior deja de ser válido de inmediato, también si después
  la entrega falla (`502`).
- Cancelar, aceptar y expirar vacían la huella: el enlace deja de ser válido y la
  aceptación posterior responde `404`.
- Ninguna operación de invitaciones recibe `reason`; los eventos de invitación
  se registran con motivo vacío.

### Listar invitaciones

`GET /farms/{id}/invitations/`

Solo el administrador activo de la finca. Devuelve un arreglo, sin paginación.

Campos: `id`, `email`, `role`, `status`, `expires_at`, `last_sent_at`,
`delivery_pending`, `accepted_at`, `cancelled_at`, `created_at`, `updated_at`.

- `200`: listado de invitaciones
- `401`: solicitud sin sesión válida
- `403`: la cuenta no es administrador
- `404`: la finca no existe o está desactivada

### Crear invitación

`POST /farms/{id}/invitations/`

```json
{
  "email": "persona@example.com",
  "role": "operador"
}
```

- `201`: invitación creada
- `400`: correo o rol inválido, o campo no permitido
- `401`: solicitud sin sesión válida
- `403`: la cuenta no es administrador
- `404`: la finca no existe o está desactivada
- `409`: ya existe una invitación vigente para el correo o se alcanzó el máximo
  de 50 invitaciones pendientes
- `429`: límite de creación excedido
- `502`: la invitación quedó `pending`, pero el proveedor rechazó la entrega

La expiración perezosa se ejecuta antes del conteo. El conteo y la creación se
realizan bajo el bloqueo de la finca.

### Cambiar el rol de una invitación

`PATCH /farms/{id}/invitations/{invitation_id}/`

```json
{
  "role": "consulta"
}
```

El cambio rota el token, renueva la vigencia y reenvía la invitación. Solo
modifica la invitación: no cambia el rol de ninguna membresía existente. Los
roles válidos son los del catálogo `GET /configuracion/roles-permisos/` (mismos
ids que `FarmRole`). Auditoría: `invitation_role_changed` con `before.role` y
`after.role`.

- `200`: rol actualizado
- `400`: rol inválido
- `401`: solicitud sin sesión válida
- `403`: la cuenta no es administrador
- `404`: la invitación no existe
- `409`: la invitación ya no está pendiente
- `429`: límite de reenvío excedido
- `502`: el cambio se guardó, pero el proveedor rechazó la entrega

### Cancelar una invitación

`DELETE /farms/{id}/invitations/{invitation_id}/`

Sin cuerpo. Pasa a `cancelled`, vacía la huella del token y registra
`invitation_cancelled`. Si la auditoría falla, la invitación sigue `pending`.

- `204`: invitación cancelada
- `401`: solicitud sin sesión válida
- `403`: la cuenta no es administrador
- `404`: la invitación no existe
- `409`: la invitación ya no está pendiente

### Reenviar una invitación

`POST /farms/{id}/invitations/{invitation_id}/resend/`

Rota el token, renueva la vigencia y reenvía el correo. Sin cuerpo. Auditoría:
`invitation_resend_started` al rotar y `invitation_resent` solo si el proveedor
acepta el correo; si lo rechaza, `invitation_delivery_failed`.

Antes de operar se expiran las invitaciones vencidas. Si la propia invitación
venció, la respuesta es `409` y la transacción se revierte entera (no se rota ni
se renueva nada); su paso a `expired` queda guardado en el siguiente listado.

- `200`: invitación reenviada
- `401`: solicitud sin sesión válida
- `403`: la cuenta no es administrador
- `404`: la invitación no existe
- `409`: la invitación ya no está pendiente
- `429`: límite de reenvío excedido
- `502`: el token se renovó, pero el proveedor rechazó la entrega

### Aceptar una invitación por enlace (heredado)

`POST /invitations/accept/`

Sigue disponible para las invitaciones enviadas antes
de F10.3.3-A, que llevaban un enlace. Las invitaciones nuevas se aceptan por
código (ver la sección siguiente).

```json
{
  "token": "<token de un solo uso>"
}
```

- `200`: membresía creada o reactivada
- `400`: token ausente o inválido para el contrato
- `401`: solicitud sin sesión válida
- `404`: invitación inválida, vencida, cancelada, aceptada o identidad no
  coincidente
- `409`: la cuenta ya pertenece activamente a la finca
- `429`: límite de aceptación excedido por usuario o por IP

Respuesta `200`:

```json
{
  "farm_id": 1,
  "membership_id": 10,
  "role": "operador",
  "status": "active"
}
```

### Invitaciones por código (F10.3.3-B)

Rutas, bajo `/api/v1/` como el resto de la API:

- `POST /invitations/code/preview/`: muestra la finca, el rol y la expiración.
  **Solo lectura**: no crea membresías ni consume el código.
- `POST /invitations/code/accept/`: crea o reactiva la membresía con el rol de la
  invitación y consume el código, todo en la misma transacción.

**Permisos:** sesión válida (`IsAuthenticated`, JWE o cookie) de una cuenta
activa con el correo verificado.

**Identidad:** la identidad y el correo salen de la cuenta autenticada. La
huella se calcula como `HMAC-SHA256(correo de la cuenta, código)`, así que un
código solo puede encontrar invitaciones dirigidas al correo de quien lo envía.

Cuerpo (serializador `InvitationCodeSerializer`). Solo admite `code`; cualquier
otro campo, por ejemplo `email`, responde `400`:

```json
{
  "code": "ABCDE-FGHJK"
}
```

Admite mayúsculas o minúsculas, con o sin guion y con espacios exteriores. El
formato lo valida `apps/farms/invitation_codes.py`.

Respuesta `200` de `preview` (`InvitationCodePreviewSerializer`):

```json
{
  "farm_name": "El Roble",
  "role": "operador",
  "expires_at": "2026-11-19T10:00:00Z"
}
```

Respuesta `200` de `accept` (`InvitationAcceptResponseSerializer`, el mismo de la
aceptación por enlace):

```json
{
  "farm_id": 1,
  "membership_id": 10,
  "role": "operador",
  "status": "active"
}
```

Ninguna respuesta incluye el código, la huella, el token, identificadores
internos de la invitación ni datos de otras personas.

| Código | Cuándo |
|---|---|
| `200` | Código válido |
| `400` | Falta `code`, está vacío o el cuerpo trae campos no permitidos |
| `401` | Sin sesión válida, o cuenta inactiva (la autenticación ya la rechaza) |
| `403` | Correo sin verificar: `{"error": "verifica tu correo para aceptar invitaciones"}` |
| `404` | `{"error": "invitación no encontrada"}`, **genérico**: código mal formado, incorrecto, inexistente, vencido, cancelado, ya usado, de otro correo, o de una finca desactivada |
| `409` | `{"error": "ya perteneces a esta finca"}`: la cuenta ya es miembro activo; no se duplica la membresía |
| `429` | Límite de intentos o de frecuencia |

**Transacción de `accept`:**
1. Bloquea la finca, luego la membresía de la cuenta y luego la invitación, el
   mismo orden que el resto de las operaciones.
2. Vuelve a comprobar el estado, la huella (con `compare_digest`), la expiración,
   el correo y que haya una sola cuenta verificada con ese correo.
3. Crea la membresía o reactiva la anterior (limpiando sus permisos) con
   **solo** el rol de la invitación.
4. Marca la invitación `accepted` y vacía `code_hash` y `token_hash`.
5. Registra `invitation_accepted` (con `metadata: {"channel": "code"}`) y
   `member_joined` o `member_reactivated`.

Si algo falla, no se confirma nada. Solo una de dos aceptaciones simultáneas
prospera; la otra recibe `404` o `409` y no crea otra membresía.

**Límites** (comunes a `preview`, `accept` y la aceptación por enlace):
- **Por cuenta:** 10 solicitudes por hora (`invitation_accept`).
- **Por IP:** 30 solicitudes por hora (`invitation_accept_ip`). Usa `REMOTE_ADDR`
  en hash y no confía en `X-Forwarded-For`; son los umbrales que ya existían.
- **Intentos fallidos:** como máximo **5 por cuenta en 15 minutos**, compartidos
  entre `preview` y `accept`. Cuenta los `404` (también los códigos mal formados)
  y los intentos bloqueados. No cuenta el `403` por correo sin verificar ni los
  códigos válidos (`200` o `409`). Al superarlo, `429` sin mirar el código.
- Los contadores están en la caché `default` de Django: Redis en producción,
  compartido entre procesos (`apps/farms/code_attempts.py`). No se guarda nada
  en MariaDB, ni el código ni la huella.

### Entrega de correo

- `REJECTED`: HTTP 502 con `{"error": "no se pudo entregar la invitación",
  "invitation_id": 12}`. La invitación permanece `pending` y `last_sent_at` no
  cambia.
- `INDETERMINATE`: respuesta normal con `delivery_pending: true`. La invitación
  permanece `pending` y no se declara entrega confirmada.
- `ACCEPTED`: `delivery_pending: false` y `last_sent_at` actualizado.

Un `502` durante la creación ocurre después de persistir la invitación. El cliente
puede recuperarla por `invitation_id` y usar el reenvío; no se crea otro registro.

### Límites de frecuencia

- Crear: 30 solicitudes por hora por administrador.
- Reenviar y cambiar rol: 10 solicitudes por hora por administrador.
- Aceptar (por enlace y por código, cuota común): 10 solicitudes por hora
  por usuario y 30 por hora por IP; además, 5 intentos fallidos con código
  por cuenta cada 15 minutos.
- El límite por IP usa `REMOTE_ADDR` y una huella SHA-256; no confía en
  `X-Forwarded-For`. Detrás de un proxy inverso sin configuración confiable,
  varios clientes pueden compartir la IP observada.

### Mantenimiento

El comando `python manage.py expire_farm_invitations` expira las invitaciones
pendientes vencidas por finca, conserva la auditoría y es idempotente. El
repositorio no incluye scheduler; el despliegue debe ejecutarlo periódicamente.

## Motivo de auditoría (`reason`)

- Límite: el de la columna `AuditEvent.reason`, 200 caracteres
  (`AUDIT_REASON_MAX_LENGTH`), contados tras recortar los espacios exteriores.
- Única entrada HTTP: `DELETE /farms/{id}/members/{membership_id}/` con cuerpo
  opcional `{"reason": "..."}`. Vacío o ausente es válido. Un motivo más largo
  responde `400` con `{"error": "el motivo no puede superar 200 caracteres"}`
  antes de iniciar la revocación.
- Los comandos `grant_farm_creation` y `revoke_farm_creation` rechazan un motivo
  más largo con el mismo mensaje, sin cambiar la capacidad.
- `record_audit_event` ya no trunca: rechaza el motivo largo con un
  `ValidationError` y, como escribe en la transacción del llamador, la operación
  completa se revierte.

## Limitaciones actuales

- El frontend implementa la aceptación y la administración visual de
  invitaciones (reenvío, cambio de rol y cancelación) con pruebas automatizadas
  sin DOM; no hay validación manual en navegador registrada.
- Las invariantes de reenvío, cambio de rol, cancelación, rotación del token y
  motivo de auditoría tienen pruebas automatizadas
  (`apps/farms/tests/test_invitation_admin_f9.py` y
  `apps/farms/tests/test_audit_reason_f9.py`); no hay validación manual
  registrada contra un proveedor de correo real.
- La autorización de escritura de inventario es fija por rol; todavía no está
  integrado el sistema configurable de permisos por módulo (DEC-05 parcial).
- No existe endpoint de actualización de productos. Cualquier actualización
  futura debe usar la misma comprobación de escritura del servicio.
- El bloqueo de fila requiere MariaDB/InnoDB; las migraciones no fijan el motor
  de las tablas. Las escrituras de una misma finca se serializan para evitar
  carreras con la baja lógica.
- La política definitiva de eliminación de cuentas queda pendiente.
