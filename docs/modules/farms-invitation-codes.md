# Códigos de invitación a fincas — F10.3.1 a F10.3.3-A

Estado:
- **F10.3.1:** base criptográfica y de modelo.
- **F10.3.2:** integración en los servicios. Crear, reenviar y cambiar el rol
  guardan una huella nueva; cancelar, aceptar y expirar la vacían.
- **F10.3.3-A:** el correo entrega el código y ya no lleva enlace.
- **F10.3.3-B:** `POST /api/v1/invitations/code/preview/` y `…/code/accept/`, con
  5 intentos fallidos por cuenta cada 15 minutos (`apps/farms/code_attempts.py`).
  El contrato está en `docs/api/farms.md`.
- **Pendiente:** conectar el frontend. Hasta entonces, el código solo se puede
  usar llamando a la API.

## Entrega del código por correo (F10.3.3-A)

### Flujo del código en memoria

1. `_new_code(email)` genera el código y su huella. Lo hace en cada intento de
   `_with_code_retry`:
   - En `create_invitation`, antes de los bloqueos.
   - En `resend_invitation` y `change_invitation_role`, tras bloquear la
     invitación, porque necesita su correo.
2. La operación guarda `code_hash`, el nonce y la reserva de entrega, y su
   transacción se confirma. La operación devuelve `(invitation, farm, code)`.
3. Fuera de toda transacción, `_finalize_delivery(code=…)` llama a
   `_send_invitation`, que arma el correo (`invitation_mail.plain` y `html`) y lo
   entrega con `deliver_email`.
4. El resultado se aplica solo si `delivery_nonce` sigue siendo el de esa entrega.
5. `del code` en un `finally` suelta la referencia. Python no garantiza borrar la
   memoria, pero no queda ninguna referencia guardada en objetos ni módulos.

Dónde no está el código:
- No se guarda en MariaDB (solo `code_hash`), ni en caché, ni en archivos.
- No aparece en logs, auditoría, respuestas de la API ni excepciones.
- No hay forma de recuperarlo después.
- **Informes de error:** `@sensitive_variables` oculta el código en los informes
  de Django cuando `DEBUG=False`, y `_send_invitation` oculta todas sus variables
  y las de las funciones que llama. Con `DEBUG=True`, la página técnica de
  Django puede mostrar variables locales; solo ocurre en desarrollo.

### Contenido del correo

- **Asunto:** «Tu código de invitación a AGROTECH-T».
- **Texto plano y HTML** con la misma identidad visual que antes:
  - la finca (escapada en HTML);
  - el rol ofrecido («Administrador», «Operador» o «Consulta»);
  - el código `XXXXX-XXXXX`;
  - la fecha de expiración en `America/Bogota`, con su desfase UTC;
  - la instrucción de entrar en AGROTECH-T con ese correo (o registrarse y
    verificarlo) y elegir «Entrar como invitado»;
  - el aviso de que el código es individual y de un solo uso, y de que no debe
    compartirse.
- **No incluye:** enlace, token, huella, identificador interno ni datos de otras
  personas.

### Invitaciones antiguas y nuevas

| | Antiguas (enviadas antes de F10.3.3-A) | Nuevas, o reenviadas o con el rol cambiado desde F10.3.3-A |
|---|---|---|
| Correo | enlace `…/invitations/accept#token=…` | código `XXXXX-XXXXX`, sin enlace |
| `code_hash` | `NULL` (no se calcula) | huella del código enviado |
| `token_hash` | el del enlace enviado; no cambia al desplegar | se sigue generando durante la transición, pero ese token **nunca se entrega** |
| Aceptación | por enlace, hasta vencer o cancelarse | por código (F10.3.3-B) |

- Reenviar o cambiar el rol de una invitación antigua le da un código nuevo y
  rota su token, así que su enlace deja de valer.
- Desplegar esta etapa no envía correos ni modifica invitaciones.
- `_accept_link` se conserva solo como definición del formato heredado.

### Entregas fallidas

| Resultado del proveedor | Qué ocurre |
|---|---|
| Rechazo (`REJECTED`) | Igual que en F9: la invitación sigue pendiente con la huella del código intentado, `delivery_pending=False`, se registra el evento `invitation_delivery_failed` y responde 502 con `{error, invitation_id}`, sin código ni huella |
| Resultado indeterminado (`INDETERMINATE`) | Se conserva la reserva (`delivery_pending=True`) |
| Excepción del proveedor | La reserva queda igual; el informe de error oculta el código |

En los tres casos se recupera con **reenviar**, que genera un código nuevo. No
existe forma de volver a ver el código anterior.

Una respuesta 502 puede corresponder a una entrega antigua que el proveedor
rechazó tarde. Aun así, esa entrega no altera el estado de la entrega vigente,
porque el nonce ya cambió.

## Integración en los servicios (F10.3.2)

| Operación (`apps/farms/invitation_services.py`) | `code_hash` | `token_hash` |
|---|---|---|
| `create_invitation` | huella nueva | token nuevo (sin cambios) |
| `resend_invitation` | huella nueva; la anterior deja de existir | token nuevo |
| `change_invitation_role` | huella nueva; la anterior deja de existir | token nuevo |
| `cancel_invitation` | `NULL` | `""` |
| `accept_invitation` (enlace) | `NULL` | `""` |
| `_expire_lazy_farm` y `expire_farm_invitations` | `NULL` | `""` |

- Cada cambio de huella o token ocurre en la misma escritura y transacción.
- En F10.3.2 el código en claro se descartaba. Desde F10.3.3-A, `_new_code` lo
  hace llegar solo al correo (ver «Entrega del código por correo»).
- Las respuestas de la API no cambian: `InvitationResponseSerializer` no expone
  ni el código ni la huella.

## Componentes

| Componente | Ubicación |
|---|---|
| Generación, normalización y huella | `apps/farms/invitation_codes.py` |
| Campo `FarmInvitation.code_hash` | `apps/farms/models.py` |
| Migración | `apps/farms/migrations/0005_farminvitation_code_hash.py` |
| Ajuste `INVITATION_CODE_HMAC_KEY` | `config/platform.py` → `config/settings.py` |
| Plantilla del correo | `apps/farms/invitation_mail.py` |
| Reglas de la clave | `config/invitation_code_key.py` |
| Pruebas | `apps/farms/tests/test_invitation_codes.py`, `test_f1031_code_hash_migration.py`, `test_invitation_codes_f1032.py`, `test_invitation_mail_f1033a.py` |

## Formato del código

- 10 caracteres del alfabeto `ABCDEFGHJKMNPQRSTUVWXYZ23456789` (31 símbolos; sin
  `0 O 1 I L`), unos 49,5 bits de entropía.
- Se presenta como `XXXXX-XXXXX`.
- `generate_code()` usa `secrets.choice`; nunca `random` ni UUID.
- El código en claro solo existe en memoria, para el flujo de entrega. Nunca se
  guarda, registra ni muestra al administrador.

## Normalización (`normalize_code`)

1. Rechaza lo que no sea `str`.
2. Quita los espacios exteriores.
3. Rechaza cualquier carácter no ASCII **antes** de pasar a mayúsculas: algunos
   caracteres Unicode se convierten en letras ASCII con `upper()` (la «s» larga
   `ſ` da `S`).
4. Quita los guiones ASCII de presentación y pasa a mayúsculas.
5. Exige exactamente 10 caracteres del alfabeto.

Los errores lanzan `InvalidInvitationCode` con un mensaje fijo, que nunca
incluye la entrada. F10.3.2 deberá traducirlo al mismo 404 genérico que un
código inexistente.

## Huella (`code_hash`)

```
HMAC-SHA256(INVITATION_CODE_HMAC_KEY, f"{correo_normalizado}:{codigo_normalizado}")
```

- Se codifica en UTF-8 y se devuelve en 64 caracteres hexadecimales en minúscula.
- Es determinista: el mismo correo y el mismo código dan la misma huella, así que
  se puede buscar por igualdad en el índice único.
- Incluir el correo liga el código al invitado: el mismo código con otro correo
  da otra huella. Un código correcto enviado desde otra cuenta es
  indistinguible de uno inexistente.
- El correo se normaliza igual que en `invitation_services._normalize_email`
  (`strip().lower()`).
- `hashes_match` compara con `hmac.compare_digest`.
- La huella no debe aparecer en respuestas, logs ni auditoría.

## Campo `code_hash`

`CharField(max_length=64, null=True, blank=True, unique=True)`, que en la base
es `VARCHAR(64) NULL` con índice `UNIQUE`.

- **NULL para las invitaciones antiguas.** Las invitaciones por enlace no tienen
  código y la migración no inventa ninguno. Calcularlo exigiría el código en
  claro, que nunca existió. Siguen aceptándose por `token_hash` hasta que venzan
  (7 días como máximo) o se cancelen.
- **Unicidad sobre los no nulos.** En MariaDB 11.8 un índice `UNIQUE` admite
  varios `NULL` (los `NULL` son distintos entre sí), así que basta `unique=True`.
  Django no aplica `UniqueConstraint(condition=…)` en MariaDB, porque no admite
  índices parciales.
- **Nunca `""`.** Invalidar un código debe poner `NULL`; dos cadenas vacías
  chocarían en el índice.
- `token_hash`, los estados, los roles, las restricciones y las relaciones no
  cambian. Consultar o listar invitaciones no genera códigos.

## Migración `farms.0005_farminvitation_code_hash`

Solo `AddField`. Su SQL se capturó en la base desechable:

- Aplicar: `ALTER TABLE farm_invitations ADD COLUMN code_hash varchar(64) NULL UNIQUE`.
- Revertir: `DROP COLUMN code_hash`, que también elimina su índice.

No hace `UPDATE`, no toca `token_hash` ni otros modelos y es reversible. **Esta
etapa no la aplica a ninguna base real**: requiere una autorización propia, con
el procedimiento de backup y comprobación de identidad de F6-REAL-MIGRATION.

## Clave `INVITATION_CODE_HMAC_KEY`

- Se lee de la variable de entorno en `config/platform.py` y se publica como
  `settings.INVITATION_CODE_HMAC_KEY`. Al ser un ajuste con «KEY» en el nombre,
  Django lo oculta en las páginas de error.
- **Fallo cerrado.** Si falta, está vacía, tiene menos de 32 caracteres o es
  igual a `SECRET_KEY`, `code_hash` lanza `ImproperlyConfigured` con un mensaje
  que nombra la variable pero no su valor.
- No hay valor por defecto, ni clave en el código, ni generación automática al
  arrancar, ni alternativa con `SECRET_KEY` (a diferencia de `OTP_HMAC_KEY`).
- **Validación al arrancar (F10.3.2).** `config/invitation_code_key.py` reúne las
  reglas y `config.settings` las aplica con `resolve_invitation_code_key`:
  - **Producción** (`DEBUG=False`, el mismo criterio que `DJANGO_SECRET_KEY`,
    `REDIS_URL` y `FRONTEND_URL`): Django no arranca si la clave falta, está vacía
    o solo tiene espacios, tiene menos de 32 caracteres o coincide con
    `SECRET_KEY`.
  - **Desarrollo y pruebas** (`DEBUG=True`; el orquestador fija
    `DJANGO_DEBUG=true`): no se exige al arrancar. Si falta, crear, reenviar o
    cambiar el rol de una invitación falla de forma cerrada con
    `ImproperlyConfigured`, sin crear ni modificar nada.
- `apps.farms.invitation_codes` reutiliza las mismas reglas en cada uso.

### Requisitos pendientes del despliegue

1. Aplicar `farms.0005_farminvitation_code_hash`, con autorización propia,
   backup y comprobación de identidad. Sin la columna, cualquier consulta de
   invitaciones falla.
2. Definir `INVITATION_CODE_HMAC_KEY` en cada entorno antes de usar
   invitaciones:
   - En el servidor local (`DEBUG=True`), sin ella no se pueden crear,
     reenviar ni cambiar invitaciones.
   - En producción, sin ella Django no arranca.

### Configuración por entorno

| Entorno | Cómo |
|---|---|
| Desarrollo | Variable de entorno del proceso de Django, definida por la persona responsable en su entorno local, fuera de Git. Clave propia de desarrollo, nunca la de producción. |
| Pruebas | Las pruebas generan una clave ficticia en cada ejecución (`secrets.token_urlsafe(48)` con el fixture `settings` de pytest-django). No se guarda en ningún archivo. |
| Producción | Gestor de secretos o variable del servicio, con acceso restringido. Nunca en el repositorio, en imágenes ni en logs. |

### Generación y custodia (procedimiento futuro)

1. Genera la clave en una máquina de confianza, sin dejarla en el historial del
   shell: `python -c "import secrets; print(secrets.token_urlsafe(48))"` (64
   caracteres, 384 bits).
2. Guárdala directamente en el gestor de secretos y anota quién la custodia.
3. Usa una clave distinta por entorno.
4. **Rotación.** Cambiar la clave invalida todos los códigos pendientes, porque
   sus huellas ya no coinciden. Hazlo solo con un plan: reenviar las invitaciones
   pendientes o aceptar que las personas deban pedir un código nuevo.
5. Si la clave se filtra, rótala y reenvía las invitaciones pendientes. El
   vínculo con el correo verificado limita el daño: el código sigue exigiendo la
   cuenta del correo invitado.

## Colisiones y reintento (implementado en F10.3.2)

Una colisión (la misma huella para dos invitaciones) es improbable
(31^10 ≈ 8·10^14 combinaciones por correo). Aun así, el índice único la convierte
en `IntegrityError` en lugar de en un código ambiguo.

**Implementación** (`_with_code_retry` y `_is_code_hash_collision`):
- Cada intento ejecuta la operación completa en su propio `transaction.atomic()`,
  que es un savepoint si ya existe una transacción exterior. Dentro genera un
  código y una huella nuevos.
- Se reintenta **solo** el error 1062 cuyo índice es exactamente `code_hash`.
  - MariaDB lo informa como `Duplicate entry '…' for key 'code_hash'`.
  - Se acepta también el prefijo `tabla.` que usa MySQL 8.
  - Lo comprueba una prueba con errores reales de la base desechable.
- Cualquier otro `IntegrityError` se propaga sin reintentar, incluido
  `uniq_farm_invitation_pending_email`. El 409 de negocio por correo duplicado
  no cambia, porque se decide antes de insertar.
- Una colisión revierte el intento entero: escrituras, expiración perezosa,
  eventos de auditoría y bloqueos. El siguiente intento vuelve a bloquear en el
  mismo orden (finca, membresía del administrador, invitación).
- Tras 3 colisiones responde `AppError("no se pudo preparar la invitación;
  inténtalo de nuevo", 500)`. El mensaje de la base, que contiene la huella, no
  se encadena ni se expone.
- El correo se envía solo después de confirmar la transacción, como antes.

Pautas para etapas futuras:

- Dentro de una transacción, no captures `IntegrityError` sin un savepoint
  (`transaction.atomic()` anidado): la transacción exterior quedaría
  inutilizable.
- Cualquier operación nueva que invalide una invitación debe poner
  `code_hash=NULL`, o una huella nueva, en la misma escritura que vacía
  `token_hash`.
