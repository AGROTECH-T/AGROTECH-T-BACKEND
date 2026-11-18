# Identidad del broker MQTT (BUILD-FLEPSI-02, modo record)

Estado: **implementado y probado en MariaDB 11.8.8 desechable. No validado con
Flespi, Render ni Layerbase. `enforce` bloqueado.** Diseño: FLEPSI-02B a 02F.

## Qué hace

1. El consumer usa **MQTT 5** (`clean_start=False`, `SessionExpiryInterval`,
   QoS 1, ACK manual tras el COMMIT, TLS obligatorio en producción).
2. En modo `record` clasifica `UserProperty` **como lista** (nunca `dict`) y
   persiste la evidencia en la misma transacción que la recepción durable:
   `broker_identity_presence`, `broker_token_key_version`,
   `broker_token_fingerprint` (HMAC-SHA256, 32 bytes). **Nunca** se guarda ni
   registra el `token_id`.
3. Las reentregas se comparan en la **versión de clave guardada** en la fila.
   Las discrepancias son eventos append-only idempotentes y nunca impiden el ACK.
4. El worker calcula un veredicto con huellas guardadas (sin claves ni token) y,
   si `enforce` rechazaría, deja `BROKER_IDENTITY_WOULD_REJECT`. **No rechaza.**

`token_id` no prueba por sí solo que la propiedad la añadió Flespi; hasta
validarlo en real, solo se registra.

## Clasificación (`apps/iot/mqtt/broker_identity.py`)

| Resultado | Condición |
|---|---|
| `absent` | sin propiedad `token_id` exacta |
| `single` | una sola, UTF-8 válido, 1..256 bytes, sin controles ni espacios exteriores |
| `multiple` | dos o más `token_id`, o una más variante de mayúsculas |
| `invalid` | valor vacío, no texto, con controles o espacios exteriores, UTF-8 inválido |
| `oversized` | más de 32 pares, o valor > 256 bytes |

Huella: `HMAC-SHA256(K_v, "agrotech-t/iot/broker-identity/v1\0" ‖ len(provider) 2B ‖ provider ‖ len(valor) 4B ‖ valor)`.

## Eventos (`IotMqttDeliveryEvent.dedupe_key`, `UNIQUE(delivery, dedupe_key)`)

| `dedupe_key` | Evento | Cuándo | Cuenta en el límite |
|---|---|---|---|
| `bi:mismatch:v<n>:<64 hex>` | `BROKER_IDENTITY_MISMATCH` | reentrega no final con otra evidencia; marca `broker_identity_conflict` | sí |
| `bi:postfinal:v<n>:<64 hex>` | `BROKER_IDENTITY_POSTFINAL` | reentrega de entrega final con otra evidencia; log ERROR; estado y mediciones intactos | sí |
| `bi:late` | `BROKER_IDENTITY_LATE` | reentrega de entrega con evidencia NULL; fila intacta | no |
| `bi:unverifiable:v<n>` | `BROKER_IDENTITY_UNVERIFIABLE` | la clave v<n> ya no está en el anillo; sin conflicto | no |
| `bi:limit` | `BROKER_IDENTITY_LIMIT` | 6.ª anomalía distinta (una vez) | — |
| `bi:overflow:v<n>:<64 hex>` | `BROKER_IDENTITY_OVERFLOW` | cada evidencia **distinta** tras el límite; suma 1 a `broker_identity_overflow` (una repetición no suma) | — |
| `bi:overflowcap` | `BROKER_IDENTITY_OVERFLOW_CAP` | más de 64 evidencias distintas tras el límite: el contador deja de crecer | — |
| (NULL) | `BROKER_IDENTITY_OBSERVATION_ERROR` | error no relacionado con la BD al clasificar/registrar identidad; solo diagnóstico | — |

Solo una colisión real de `iot_mqtt_event_dedupe_uniq` se trata como
duplicado: se exige el código estructurado 1062 **y** que, tras revertir el
*savepoint*, exista ese `(delivery, dedupe_key)`; no se depende del texto
(posiblemente localizado) del mensaje. Cualquier otro error de BD hace rollback
y no hay ACK.

### Aislamiento de errores de observación (BUILD-FLEPSI-02H)

- Entrega nueva: la evidencia se calcula **antes** de la transacción (código
  puro). Si falla, la evidencia queda NULL (nunca se inventa una identidad; la
  entrega queda exenta del veredicto) y se registra
  `BROKER_IDENTITY_OBSERVATION_ERROR` en la misma transacción.
- Reentrega: el registro de identidad corre en un *savepoint*. Un error ajeno a
  la BD lo revierte (evidencia, conflicto y contadores intactos), deja el evento
  de diagnóstico y la reentrega se confirma normalmente.
- `DatabaseError` (incluido `IntegrityError`) se propaga siempre: sin ACK.
- El ACK sigue enviándose solo después del COMMIT.

### Bloqueo de fila en reentregas (H-04, se mantiene)

En `record`, toda reentrega bloquea la fila (`select_for_update`) antes de
insertar cualquier evento. Motivo: serializar con el worker, que también bloquea
la fila durante la ingesta, y con otras reentregas, para que conflicto, límite y
overflow sean exactos. Coste: una reentrega puede esperar al COMMIT del worker;
si se agota `innodb_lock_wait_timeout`, el resultado es PERSISTENCE_ERROR (sin
ACK) y el broker reentrega. Se conserva porque las reentregas son raras (solo
sin PUBACK) y la exactitud prima sobre la latencia en esta fase.

## Modos (`IOT_BROKER_IDENTITY_MODE`)

| Modo | Comportamiento |
|---|---|
| `off` (por defecto) | sin captura, sin clave, sin eventos `bi:*`, sin veredicto |
| `record` | captura, eventos, `WOULD_REJECT`; nunca rechaza |
| `enforce` | **no seleccionable**: `mqtt_consumer`, `mqtt_delivery_worker` y `mqtt_broker_identity` fallan con `ImproperlyConfigured` (código 1). También falla un `BrokerIdentityConfig(ENFORCE)` construido a mano, el consumer/worker que lo reciba y el worker en cada lote si el entorno dice `enforce`; nunca se degrada a `off` |

Las entregas con evidencia NULL (históricas o recibidas en `off`) no se
modifican ni se evalúan.

## Claves (independientes; nunca en la base ni en logs)

| Variable | Contenido |
|---|---|
| `IOT_BROKER_IDENTITY_HMAC_KEY` | Base64 de 32 bytes (vigente) |
| `IOT_BROKER_IDENTITY_HMAC_KEY_VERSION` | 1..65535 |
| `IOT_BROKER_IDENTITY_HMAC_KEY_PREVIOUS` | `v:base64,...` (solo comparar) |
| `IOT_BROKER_IDENTITY_HMAC_KEY_NEXT` | `v:base64` (solo registrar huellas) |
| `MQTT_SESSION_EXPIRY_SECONDS` | 60..4294967295 (por defecto 86400) |

Generación local (no pegar en chat ni repositorio):
`python -c "import secrets,base64;print(base64.b64encode(secrets.token_bytes(32)).decode())"`.
El arranque rechaza claves iguales entre versiones o iguales a `SECRET_KEY`,
`GATEWAY_CREDENTIAL_HMAC_KEY`, `INVITATION_CODE_HMAC_KEY` o
`COORDINATOR_KEY_ENCRYPTION_KEY[_PREVIOUS]`.

## Registro y rotación (`python manage.py mqtt_broker_identity ...`)

El `token_id` se pasa **solo por stdin** (`--token-stdin`): en una terminal se pide sin eco (`getpass`); desde una tubería se lee una línea.

```text
register        --gateway-uid GW-... --operator op --token-stdin   # huella vigente (+NEXT)
register-next   --gateway-uid GW-... --operator op --token-stdin   # verifica contra la vigente
revoke          --gateway-uid GW-... --operator op --cause rotation|compromise|decommission|admin --confirm GW-...
missing [--version N]   versions   activation-check   retirement-check --version N   # solo lectura
```

Procedimiento de rotación v → v+1 (una sola instancia de consumer):

1. Configurar `..._NEXT=v+1:<clave>` y desplegar sin cambiar la vigente.
2. `register-next` para cada identidad activa.
3. `activation-check` debe devolver `missing: 0` (si no, código 1).
4. Detener el consumer con SIGTERM; confirmar cero instancias vivas.
5. Vigente = v+1, `..._PREVIOUS` incluye v, `..._NEXT` vacío; arrancar una
   instancia; verificar en el log `vigente=v<v+1>` y `session_present=True`.
6. Retirar v solo cuando `retirement-check --version v` cumpla los criterios
   objetivos (0 entregas no finales en v, 0 identidades activas sin huella
   vigente, v no cargada como vigente/NEXT) y el propietario apruebe la
   retención de auditoría.

Invariante: toda instancia viva dispone de todas las versiones que otra
instancia viva pueda usar como vigente. Las huellas por versión no tienen ruta
de borrado (ORM + triggers).

## Base de datos

- `0017`: columnas de evidencia, `dedupe_key` (`ascii_bin`, CHECK, UNIQUE).
- `0018`: `iot_broker_identities`, `iot_broker_identity_fingerprints`.
- `0020`: amplía el CHECK de `dedupe_key` con `overflow`/`overflowcap` (nuevo antes de retirar el antiguo); barrera de reversión.
- `0019`: triggers `BEFORE UPDATE/DELETE` en `iot_mqtt_delivery_events` y
  `iot_broker_identity_fingerprints` (bloquean también el borrado en cascada
  del ORM) y barrera de reversión (`_rollback_guard`).
- Permisos para migrar: los ya exigidos por 0012/0013 (`TRIGGER`; con binlog,
  `SUPER` o `log_bin_trust_function_creators=1`). Pendiente confirmar con
  Layerbase; no se modificaron permisos.

## Pendiente de validación externa

- Flespi: existencia y formato de `token_id` en MQTT 5; qué ocurre con un
  `token_id` enviado por el publicador (condición para `enforce`); ACL por topic;
  `SessionExpiryInterval` máximo y cola.
- Render: solapamiento de instancias del Background Worker al desplegar.
- Layerbase: `TRIGGER`, binlog, MariaDB 11.8.
- E2E con broker local: no ejecutada (no autorizada).

## Estados de evidencia y veredicto (BUILD-FLEPSI-02J)

Sin cambios de esquema. El estado se deduce de la fila y de eventos append-only:

| Estado | Condición |
|---|---|
| Observada | `broker_identity_presence` no NULL |
| No capturada (histórica u `off`) | NULL y sin diagnóstico de fase `receipt:` |
| Observación fallida | NULL y `BROKER_IDENTITY_OBSERVATION_ERROR` con `result_code` `receipt:<etapa>:<tipo>` (solo se crea en la transacción que crea la entrega) |

`bi:late` solo se registra para "no capturada"; una entrega con observación
fallida no genera `bi:late` en sus reentregas.

`identity_verdict` devuelve `IdentityVerdict(status, reason)`; solo `MATCH` es
una identidad verificada:

| `status` | `reason` | En record |
|---|---|---|
| `match` | `match` | nada |
| `mismatch` | `mismatch` | `WOULD_REJECT` |
| `unverifiable` | `missing`, `ambiguous`, `invalid`, `unregistered`, `invalid_topic` | `WOULD_REJECT` |
| `not_observed` | `not_observed` | `WOULD_REJECT` |
| `not_applicable` | `not_captured` | nada |

Política futura de `enforce` (**documentada, no implementada**): aceptar solo
`match`; rechazar `mismatch`, `unverifiable` y `not_observed`; `not_applicable`
solo exento para `received_at < ENFORCE_SINCE` (después debe rechazarse, porque
`enforce` exige captura).

## Conteo por huella entre categorías (J-03)

La evidencia se identifica por `v<n>:<huella>`. Antes de contar se busca esa
huella en `mismatch`, `postfinal` y `overflow`. Una evidencia ya contada que
reaparece tras el estado final deja como mucho un `bi:postfinal` **contextual**
(no consume el límite, no toca conflicto ni overflow). El límite de 5 cuenta
huellas distintas, no eventos.

## Diagnósticos deduplicados (J-06)

`BROKER_IDENTITY_OBSERVATION_ERROR` usa `result_code =
"<receipt|redelivery>:<etapa>:<tipo>"` y no se repite para la misma entrega;
un diagnóstico distinto sí se registra. Sin índice nuevo: la comprobación
previa es serializable porque la entrega nueva se crea en la misma transacción
y la reentrega bloquea la fila. Un `DatabaseError` al registrarlo impide el ACK.

## Cierre de AUDIT-FLEPSI-02K (BUILD-FLEPSI-02K-CORRECCIONES)

**Precondición de concurrencia: READ COMMITTED (K-H1).** La deduplicación de
diagnósticos y los límites de 5 y 64 comprueban existencia/recuento *después*
de bloquear la fila de la entrega (`select_for_update`). Solo es correcto si
cada sentencia ve lo último confirmado; con REPEATABLE READ una lectura previa
(p. ej. la del coordinador) fijaría una instantánea antigua. Por eso:

- `config/database.py` declara `OPTIONS["isolation_level"] = "read committed"`
  (igual al valor por defecto de Django; sin cambio efectivo).
- `apps.iot.db_guard.require_read_committed()` falla cerrado en `mqtt_consumer`
  y `mqtt_delivery_worker` si la configuración efectiva es otra.
- Pendiente: comprobar `@@session.transaction_isolation` en una sesión real de
  Layerbase (no validado).

**Diagnósticos con `dedupe_key` NULL (K-H2).** Sin respaldo UNIQUE (MariaDB
admite varios NULL); la unicidad depende del bloqueo de fila + READ COMMITTED.
Toda ruta futura que inserte `BROKER_IDENTITY_OBSERVATION_ERROR` debe bloquear
antes la entrega o crearla en la misma transacción.

**Contexto overflow -> final (K-H3).** Una huella registrada como overflow que
reaparece tras el estado final deja un único `bi:postfinal` contextual
("como overflow"). No consume el límite de 5: el conjunto contado es
`(mismatch ∪ postfinal) − overflow`.

**`result_code` acotado (K-H4).** Si `<fase>:<etapa>:<tipo>` supera 64
caracteres se guarda un prefijo + `~` + 16 hex de SHA-256 del valor completo:
determinista, sin fusiones por prefijo, sin cambio de esquema ni de los
valores cortos existentes.

**Límite real de 64.** Validado en MariaDB desechable con la constante real
(sin parche): 63 → 64 almacenada → la 65.ª no suma y genera un único
`bi:overflowcap`; repeticiones y concurrencia no lo superan.
