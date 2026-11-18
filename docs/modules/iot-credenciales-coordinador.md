# IoT-03.2 - Ciclo de vida de las credenciales de coordinador

Implementación de las claves HMAC de coordinador previstas en
[DEC-13](../decisions/DEC-13-arquitectura-iot.md) §13 y en
[iot-seguridad.md](iot-seguridad.md) §2.2. Sustituye los pendientes P-IOT-20 y
P-IOT-22; el procedimiento físico de carga (P-IOT-21) sigue pendiente.

## 1. Propósito

Emitir, rotar, revocar y verificar la clave HMAC-SHA256 con la que cada
coordinador firma sus tramas LoRa. La clave se guarda **cifrada en reposo** con
AES-256-GCM y se entrega una sola vez. El backend también mantiene la ventana de
arranques recientes que usa el antirreplay de IoT-04. BUILD-IOT-02 expone su
ciclo administrativo por la API autenticada de aprovisionamiento. La integración
broker/Mosquitto permanece sujeta a la configuración del despliegue.

## 2. Especificación técnica

### 2.1 Componentes

- `config/coordinator_key_encryption_key.py`: validación de
  `COORDINATOR_KEY_ENCRYPTION_KEY` (Base64 de exactamente 32 bytes), versión
  vigente y versiones históricas; anillo de claves `MasterKeyRing`.
- `apps/iot/coordinator_credentials.py`: primitivas puras (generación,
  cifrado/descifrado, AAD, HMAC de trama y validación de la trama interpretada).
- `apps/iot/lora_frame.py`: parser binario v1 de la trama LoRa, implementado en
  IoT-04.2 ([contrato LoRa §2.8](iot-contrato-lora.md)).
- `apps/iot/services/coordinator_credentials.py`: servicios con autorización,
  bloqueo, auditoría y ventana antirreplay.
- Modelos `CoordinatorCredential`, `CoordinatorKeyGeneration` y
  `CoordinatorBootWindow` (`iot.0004`, `iot.0009`); `iot.0005` renombra la
  versión persistida a `master_key_version`.
- Eventos `iot_coordinator_credential_*` (`trazabilidad.0011` y `0012`).

### 2.2 Clave, AAD y cifrado

- **Clave del coordinador:** 32 bytes de `secrets.token_bytes(32)`. Solo se
  devuelve una vez, en `IssuedCoordinatorCredential`; `provisioning_key()` la
  entrega en Base64 para la carga física.
- **Cifrado:** AES-256-GCM. Nonce aleatorio de 12 bytes en su propio campo. La
  etiqueta GCM (16 bytes) viaja dentro del ciphertext, por lo que una clave de 32
  bytes produce **48 bytes de ciphertext**.
- **AAD:** `agrotech:co-cred:v1:{coordinator_uid}:{master_key_version}`. Liga el
  secreto al coordinador, al dominio y a la versión de la clave maestra:
  modificar cualquiera de los tres, el nonce o el ciphertext hace fallar la
  autenticación.
- **Clave maestra:** `COORDINATOR_KEY_ENCRYPTION_KEY` es Base64 que debe
  decodificar a 32 bytes. No hay valor por defecto ni alternativa con
  `SECRET_KEY`. `COORDINATOR_KEY_ENCRYPTION_KEY_VERSION` indica la versión
  vigente; `COORDINATOR_KEY_ENCRYPTION_KEY_PREVIOUS` publica versiones
  históricas con formato `version:base64[,version:base64]`. Una credencial se
  descifra con la versión con la que se creó, sin asumir la actual. La versión
  vigente debe ser un entero entre **1 y 65535** (límite de
  `SMALLINT UNSIGNED` en MariaDB); fuera de ese rango la emisión falla de forma
  cerrada y no escribe credencial ni auditoría.
- **Arranque:** en producción (`DEBUG=False`) la clave se valida al arrancar. En
  desarrollo y pruebas las operaciones criptográficas fallan de forma cerrada si
  la configuración no es válida.

### 2.3 Modelo `CoordinatorCredential` (`iot_coordinator_credentials`)

| Campo | Regla |
| --- | --- |
| `ciphertext` | 48 bytes; `CHECK` de longitud |
| `nonce` | 12 bytes; `CHECK` de longitud |
| `master_key_version` | Versión de la clave maestra usada al cifrar; `CHECK` entre 1 y 65535; no es la `key_version` de la trama LoRa |
| `key_version` | Generación de la credencial (uint16, 1-65535); `NULL` solo en credenciales legacy no regularizadas. No se confunde con `master_key_version` |
| `active_slot` | `1` en la vigente, `NULL` en las revocadas |
| `issued_at`, `issued_by` | Emisión y administrador |
| `revoked_at`, `revoked_by`, `revocation_cause` | Revocación coherente con el estado |
| `coordinator` | `PROTECT`; sin `farm`, la finca la determina el coordinador |

Restricciones: `UNIQUE (coordinator, active_slot)` garantiza una sola credencial
vigente por coordinador (MariaDB admite varios `NULL`) y conserva el historial;
`UNIQUE (coordinator, key_version)` impide reutilizar una generación; `CHECK`
1-65535 con `NULL` permitido; `CHECK` de estado con `IS NOT NULL` explícito;
`revoked_at >= issued_at`. Una credencial revocada **nunca** vuelve a activarse:
se emite otra. Sin caducidad automática. La restricción de estado exige causa y
fecha cuando la credencial está revocada; `revoked_by` puede quedar `NULL`
(`SET_NULL`), de modo que borrar la cuenta del administrador no borra el
historial de revocación.

El límite físico de `key_version` es `SMALLINT UNSIGNED` (0-65535). Un valor
`0` incumple el `CHECK` `iot_coordcred_generation_range` y MariaDB lo rechaza
con el error `4025`, que Django expone como `IntegrityError`. Un valor `65536`
se rechaza antes de evaluar el `CHECK`, por desbordamiento del tipo, con el
error `1264`, que Django expone como `DataError`. Por eso la prueba de rango
distingue ambas excepciones y comprueba el código del controlador.

`CoordinatorKeyGeneration` (`iot_coordinator_key_generation`) guarda por
coordinador el contador durable `last_key_version` y su `updated_at`. Se crea en
la primera emisión (generación 1), solo avanza y nunca se reinicia ni se
reutiliza. La migración `iot.0009` es transitoria: no rellena credenciales
legacy y mantiene `key_version` nullable.

`CoordinatorBootWindow` (`iot_coordinator_boot_window`) guarda, por coordinador,
los arranques recientes con `first_seen_at` y `last_seen_at`; `UNIQUE
(coordinator, boot_counter)` y un índice por `-boot_counter`.

### 2.4 Operaciones

Escritura: autorización → `require_mariadb()` → validación → operación y
auditoría, dentro de una transacción. Orden de bloqueo: Farm → FarmMembership →
Coordinator → credenciales y dependencias. Solo el administrador de una finca
activa.

- **Emitir:** falla con `409` si el coordinador ya tiene credencial vigente. La
  primera emisión crea el contador en la generación 1; después de una revocación,
  la siguiente emisión continúa desde el contador (nunca reinicia).
- **Rotar:** revoca la vigente y emite la nueva con la generación siguiente en la
  misma transacción. Causas: `rotated` (ordinaria) o `reissued` (clave perdida).
  **Sin coexistencia.** Una reemisión reinicia además la ventana antirreplay del
  coordinador en esa misma transacción, para que el dispositivo reemplazado pueda
  volver a `boot_counter` 0; el número de filas borradas queda en la auditoría
  como `boot_window_rows_reset`. La rotación ordinaria no toca la ventana.
- **Legado sin contador:** si el coordinador tiene credenciales pero no existe
  `CoordinatorKeyGeneration`, emitir o rotar falla cerrado con
  `LegacyGenerationUnresolved` (409), sin backfill ni escrituras parciales. Una
  credencial vigente con `key_version` `NULL` no autentica.
- **Agotamiento:** al llegar a 65535 se detienen emisión y rotación con
  `CoordinatorGenerationExhausted` (409); la credencial vigente no se revoca, el
  contador no desborda y se audita
  `iot_coordinator_credential_generation_exhausted`.
- **Revocar:** causas `admin` o `compromised`. Una credencial ya revocada no se
  vuelve a revocar (`404`).
- **Desactivar el coordinador:** `devices.deactivate_coordinator` revoca todas
  sus credenciales vigentes con causa `coordinator_deactivated` y audita
  `iot_coordinator_credential_cascade_revoked`. Reactivar no las restaura.
- **Historial:** `list_coordinator_credentials` exige lectura de la finca y no
  devuelve claves.

Causas: `rotated`, `reissued`, `compromised`, `coordinator_deactivated`,
`admin`. Es un enum independiente del de gateway.

### 2.5 Verificación de tramas

`verify_coordinator_frame(frame, hmac_context)` recibe la trama **ya
interpretada**: un `ParsedCoordinatorFrame`, que se obtiene con
`parse_lora_frame(raw).to_coordinator_frame()` (`apps/iot/lora_frame.py`,
IoT-04.2).

1. Valida la forma: `coordinator_uid` `CO-` válido, enteros no negativos, bytes
   firmados y tag de **exactamente 32 bytes** (`LORA_TAG_BYTES`). Después
   (IoT-04.2) vuelve a interpretar `signed_bytes + tag` con el parser v1 y exige
   que `coordinator_uid`, `key_version`, `boot_counter`, `seq`, `signed_bytes` y
   `tag` coincidan exactamente con lo declarado (M-02). Todo esto ocurre sin
   consultar la base.
2. Busca la credencial vigente del coordinador.
3. Descifra la clave con la versión de la credencial.
4. Verifica `HMAC-SHA256(contexto || bytes_firmados)` completo con
   `compare_digest`.
5. Solo después del HMAC, compara `key_version` con la generación persistida de
   la credencial. Una generación anterior, futura o `NULL` no autentica.

Devuelve la identidad resuelta desde el coordinador registrado (incluidos
`credential_id`, `farm_id` y la generación validada) o `None`. No distingue el
motivo del rechazo y no escribe nada.
`hmac_context` sigue siendo un argumento obligatorio; para la trama v1 es
`LORA_FRAME_HMAC_CONTEXT` (`agrotech:lora-frame:v1`, D-10 de IoT-04.1). **Tag (P-IOT-12, resuelto en IoT-03.2-R3):** el HMAC-SHA256
completo de exactamente 32 bytes, fijado por el backend en la constante
`LORA_TAG_BYTES`. La longitud nunca se deduce de la trama: un tag de 1, 8, 16, 31,
33 o cualquier otra longitud distinta de 32 se rechaza antes de consultar la base,
y el HMAC esperado nunca se recorta. Cuando no hay credencial o el ciphertext no autentica, se calcula un
HMAC descartable con una clave ficticia para que el tiempo de rechazo no revele
la causa.

**Contrato de `signed_bytes`:** `coordinator_uid`, `key_version`,
`boot_counter`, `seq` y las lecturas van dentro de `signed_bytes`
([contrato LoRa §2.7](iot-contrato-lora.md)). `signed_bytes` **no se
reconstruye**: se vuelve a interpretar para comprobar que los campos declarados
son coherentes con él. La coherencia no demuestra autenticidad; la autenticidad
la da solo la verificación HMAC del paso 4. Las lecturas no forman parte de
`ParsedCoordinatorFrame` y solo las protege el HMAC. La comparación de
`key_version` ocurre después del HMAC (paso 5) para no revelar la generación
vigente a una trama no autenticada.

**Requisitos obligatorios de la auditoría IoT-03.2-R2:**

- **M-02, campos ligados a la firma — resuelto en IoT-04.2.** `coordinator_uid`, `key_version`,
  `boot_counter` y `seq` deben **derivarse de los mismos `signed_bytes`** que se
  verifican, mediante un único parser. No se aceptan valores independientes que
  puedan contradecir lo firmado. IoT-04 debe incluir pruebas de manipulación de
  cada campo, y cada una debe terminar en rechazo. *Implementado:*
  `validate_coordinator_frame` reanaliza los bytes con el parser v1 y las
  pruebas de manipulación de IoT-04.2 terminan en rechazo.
- **M-03, verificación y aceptación atómicas — pendiente.** La verificación
  (`verify_coordinator_frame`) y la aceptación (`accept_coordinator_sequence`)
  deben ejecutarse en una misma operación transaccional. Antes de aceptar se
  revalida bajo bloqueo que la credencial verificada sigue vigente; una trama
  cuya credencial se revocó entre ambos pasos se rechaza. IoT-04 debe incluir
  una prueba de esa carrera. `AuthenticatedCoordinatorFrame` es público: nada
  impide construirlo sin autenticar, así que solo debe crearlo la verificación.

### 2.6 Antirreplay

`accept_coordinator_sequence(frame, now=None)` mantiene una ventana de **cuatro
arranques**: el mayor visto y los tres anteriores.

- Un arranque mayor abre la ventana y elimina los más antiguos.
- Un arranque dentro de la ventana se acepta aunque llegue tarde; el duplicado
  solo actualiza `last_seen_at`.
- Uno anterior a `mayor - 3` se rechaza con `SequenceOutOfWindow`.
- Las secuencias desordenadas dentro de un mismo arranque se aceptan: aquí no se
  guarda `seq`.

Antes de tocar la ventana, la aceptación revalida la identidad persistida:
`coordinator_id`, `coordinator_uid`, `farm_id`, `credential_id` y `key_version`
deben ser válidos; coordinador, `uid` y `farm_id` deben coincidir con el
coordinador bloqueado por `farm_id` y `uid`; el coordinador y su finca deben
estar activos. Un frame manipulado, un coordinador de otra finca o un
coordinador inactivo se rechaza **sin** crear ni modificar la ventana. Así la
aceptación de secuencia no sirve para atribuir mediciones a otra finca.

**Dependencia de la ingestión (IoT-04, pendiente):** esta función no deduplica.
La identidad única aprobada en IoT-04.1 (D-01),
`(coordinator_id, credential_id, boot_counter, seq)`, la inserción de la
medición y esta aceptación deben compartir una sola transacción; la identidad
`UNIQUE` y la persistencia de tramas/mediciones siguen pendientes. Un fallo de
persistencia no puede dejar la ventana avanzada. Exige `transaction.atomic()`
del llamador y lanza `RuntimeError` si no existe. La ventana se actualiza
después de autenticar y antes de confirmar la medición. La reemisión (`reissued`)
la reinicia en la misma transacción para permitir un `boot_counter` 0 legítimo.

### 2.7 Finca y aislamiento

La finca nunca viaja en la trama. Se obtiene del coordinador registrado; el
servicio de IoT-04 deberá comprobar además que coincide con la del gateway. Una
credencial de un coordinador de otra finca no se puede emitir, rotar, revocar ni
listar desde una finca ajena (`404`), y su AAD impide reutilizarla en otro
coordinador. `accept_coordinator_sequence` revalida coordinador y finca antes de
aceptar el arranque: no acepta frames cuyo `coordinator_id`, `uid` o `farm_id`
no coincidan con la fila persistida.

### 2.8 Auditoría

Eventos: `iot_coordinator_credential_issued`, `..._rotated`, `..._revoked`,
`..._compromised`, `..._cascade_revoked` y
`..._generation_exhausted`. Los datos auditados usan `coordcred_id`,
`coordinator_uid`, `key_version`, `master_key_version`, `active`,
`revocation_cause`, `previous_coordcred_id`, `boot_window_rows_reset`,
`revoked_coordcreds` y, en el agotamiento, `operation`/`last_key_version`.
Nunca se auditan la clave, el ciphertext, el nonce ni material HMAC. La protección de `record_audit_event` no se relaja: cualquier
nombre de clave que contenga un término sensible se rechaza.

### 2.9 Protección del secreto

- La clave solo existe en memoria durante emisión y verificación.
- `sensitive_variables` envuelve las funciones que reciben material secreto.
- `repr` de los resultados oculta clave, ciphertext, nonce, bytes firmados y tag.
- El aprovisionamiento devuelve la clave una sola vez; no se registra ni se
  persiste en claro.

### 2.10 Despliegue y rotación de la clave maestra

Producción no arranca sin `COORDINATOR_KEY_ENCRYPTION_KEY` válida. Para rotarla:
publicar la versión anterior en `COORDINATOR_KEY_ENCRYPTION_KEY_PREVIOUS`,
subir `COORDINATOR_KEY_ENCRYPTION_KEY_VERSION` y la nueva clave, y no retirar la
anterior hasta que no queden credenciales con esa versión. Perder una versión
publicada hace ilegibles las credenciales cifradas con ella.

### 2.11 Política de reversión de `iot.0009`

`iot.0009` añade `key_version` y la tabla `iot_coordinator_key_generation`. Su
reversión elimina ambos. La migración se mantiene reversible: esa propiedad es
necesaria para pruebas y entornos locales, y hacerla fallar no protegería los
datos, porque la tabla seguiría pudiendo borrarse manualmente. La protección es
operativa, no estructural.

- **Desarrollo y pruebas desechables:** revertir `iot.0009` solo es aceptable en
  bases desechables o locales sin credenciales reales. Se pierden las
  generaciones emitidas; después hay que recrear el entorno o volver a
  aprovisionar los coordinadores.
- **Producción:** no revertir `iot.0009` en caliente. El esquema de `0009` es
  compatible con el código de `0008` (`key_version` nullable y tabla adicional),
  de modo que una vuelta atrás de la aplicación debe mantener la migración
  aplicada. Si fuera imprescindible revertir el esquema, exportar antes
  `iot_coordinator_key_generation` y el máximo `key_version` por coordinador,
  tratar la operación como destructiva y restaurar esos valores antes de emitir
  o rotar de nuevo.
- **Nunca** restaurar una copia de seguridad que reduzca `last_key_version` por
  debajo del máximo histórico ya emitido. Tras una reversión el contador no se
  reconstruye solo: mientras no se restaure, emitir y rotar quedan bloqueados
  por el fail-closed de legado y no se reutiliza ninguna generación.
- Una reversión de código no implica una reversión segura de datos: la
  generación es el máximo histórico y debe sobrevivir a la versión que la creó.

## 3. Explicación sencilla

Cada coordinador tiene una llave secreta para firmar lo que envía. El backend no
puede guardar esa llave tal cual, así que la guarda dentro de una caja fuerte
digital (AES-256-GCM) y solo la abre cuando llega una trama. Al cambiar la llave
se rompe la anterior de inmediato. Si el coordinador se reinicia muchas veces, el
backend recuerda solo los cuatro arranques más recientes para que no se repitan
tramas viejas.

## 4. Ejemplo práctico

```python
issued = issue_coordinator_credential(
    user=admin, farm_id=farm_id, coordinator_uid="CO-7K3M9QXT2A"
)
clave_para_cargar = issued.provisioning_key()  # se muestra una sola vez

rotada = rotate_coordinator_credential(
    user=admin, farm_id=farm_id, coordinator_uid="CO-7K3M9QXT2A"
)  # la anterior queda revocada en la misma transacción
```

La trama autenticada no se guarda aquí. La interpretación ya existe
(`parse_lora_frame`, IoT-04.2):

```python
frame = parse_lora_frame(raw).to_coordinator_frame()
autenticada = verify_coordinator_frame(
    frame=frame, hmac_context=LORA_FRAME_HMAC_CONTEXT
)
```

Lo que sigue pendiente es la ingestión: llamar a `accept_coordinator_sequence`
dentro de la misma transacción que la deduplicación y la persistencia de la
medición (M-03 incluido).

## 5. Decisiones aprobadas

1. AES-256-GCM; clave de 32 bytes; nonce de 12 bytes separado; tag GCM dentro del
   ciphertext (48 bytes).
2. AAD `agrotech:co-cred:v1:{coordinator_uid}:{master_key_version}`.
3. Clave maestra Base64 de exactamente 32 bytes, versionada, independiente de
   `SECRET_KEY`; obligatoria y validada al arrancar en producción.
4. Rotación inmediata, sin coexistencia; reemisión con causa `reissued`.
5. Al desactivar el coordinador se revocan sus credenciales; reactivar no las
   restaura.
6. Causas en un enum independiente del de gateway.
7. Ventana antirreplay de cuatro arranques; la deduplicación de `seq` y la
   transacción conjunta son de IoT-04. *(Nota histórica IoT-04.1: identidad de
   deduplicación fijada por D-01, `(coordinator_id, credential_id, boot_counter,
   seq)`; posteriormente implementada en la persistencia de ingesta IoT.)*
8. El contexto HMAC (P-IOT-06) no se inventa: se recibe como parámetro. El tag
   (P-IOT-12) es el HMAC-SHA256 completo de 32 bytes, sin truncar (IoT-03.2-R3).
   *(Nota IoT-04.1: contexto resuelto por D-10, `agrotech:lora-frame:v1`; sigue
   recibiéndose como parámetro.)*
9. La versión persistible de la clave maestra está limitada al rango del campo
   MariaDB (1–65535); un `IntegrityError` solo se traduce a `409` si el conflicto
   es la credencial vigente real, nunca por otras restricciones.
10. Reemitir reinicia la ventana antirreplay en la misma transacción; rotar no.
11. La aceptación de secuencia revalida `coordinator_id`, `uid`, `farm_id`,
    `credential_id` y `key_version` contra la base antes de tocar la ventana.
12. `key_version` es una generación uint16 por coordinador, persistida en la
    credencial y en un contador independiente; solo se compara después del HMAC.
    Al agotarse se detienen emisión y rotación sin desbordar ni reutilizar.
13. `iot.0009` permanece reversible por necesidad de pruebas y desarrollo, pero
    en producción no se revierte el esquema al volver a una versión anterior de
    la aplicación: la generación es un máximo histórico que debe conservarse
    (§2.11).

## 6. Aspectos pendientes

- **P-IOT-06: parcial.** Contexto y estructura binaria aprobados (IoT-04.1);
  parser v1 implementado (IoT-04.2). Pendientes el tamaño máximo y la validación
  física.
- **P-IOT-12: resuelto en IoT-03.2-R3:** HMAC-SHA256 completo, 32 bytes, sin
  truncamiento, longitud fijada por el backend.
- **M-02: resuelto en IoT-04.2** (§2.5).
- **M-03:** pendiente; requisito obligatorio de la ingestión (§2.5).
- **Generaciones de credencial: resuelto en IoT-04.3-C1** (§2.3–§2.5):
  `key_version`, `CoordinatorKeyGeneration`, comparación tras el HMAC y
  `credential_id` en `AuthenticatedCoordinatorFrame`.
- **P-IOT-21:** procedimiento físico de carga de la clave en el coordinador.
- **IoT-04:** deduplicación de `seq`, persistencia de mediciones y transacción
  conjunta con la ventana antirreplay.
- **IoT-05:** `last_used_at` u observabilidad equivalente.
- Integración MQTT/Mosquitto (P-IOT-24) y cifrado de flash/arranque seguro del
  ESP32 (P-IOT-23).

## 7. Riesgos y consideraciones

- Perder una versión de la clave maestra inutiliza las credenciales cifradas con
  ella; hay que respaldarla y retirar versiones solo cuando ya no se usen.
- La rotación inmediata corta en seco a un coordinador que no haya recibido la
  clave nueva; el procedimiento operativo debe coordinarlo.
- La ventana de cuatro arranques no impide repetir un `seq` dentro del mismo
  arranque: esa garantía exige la identidad única de IoT-04
  (`(coordinator_id, credential_id, boot_counter, seq)`, pendiente).
- El antirreplay no sustituye la firma: primero se autentica la trama y solo
  después se acepta el arranque.
