# IoT-01 — Persistencia, confirmaciones, reintentos y deduplicación

- **Etapa:** IoT-01 (solo documentación)
- **Estado de implementación:** El modelo, las migraciones y el servicio de
  ingesta transaccional existen en el repositorio. No se verificó su estado en
  una base de datos conectada. `apps/iot/services/ingestion.py` es el único
  escritor de ingesta; MQTT delega en él.
- **Decisión asociada:** [DEC-13](../decisions/DEC-13-arquitectura-iot.md)
- **Fecha:** 2026-11-12 (actualizado el 2026-11-13, IoT-04.3-A)

## 1. Propósito

Definir cuándo se considera guardada una medición, cómo se confirman y
reintentan los mensajes en cada tramo y cómo se evita guardar duplicados.

## 2. Especificación técnica

### 2.1 Confirmaciones por tramo

| Tramo | Mecanismo | Si falla |
|---|---|---|
| Coordinador → gateway | ACK LoRa opcional (P-IOT-08) | Sin ACK, la trama se pierde. Con ACK, el coordinador reintenta con el **mismo** `seq`. |
| Gateway → Mosquitto | PUBACK QoS 1 | El gateway conserva la trama en el búfer y reenvía. |
| Mosquitto → consumidor | PUBACK QoS 1 manual, **después del `COMMIT`** | El broker reenvía al reconectar (sesión persistente). |
| Django (servicio de ingesta) → MariaDB | Transacción (`transaction.atomic()`) | `ROLLBACK`, sin PUBACK y reintento con espera creciente. |

No existe un Writer independiente ni un transporte Django → Writer en la
arquitectura vigente. El servicio integra UUIDv5 y SHA-256 de B0 en memoria: para
reentregas compara la huella calculada para la solicitud con otra reconstruida desde los datos
semánticos de `ReceivedFrame`. La huella no se persiste y el esquema actual no
conserva todos los resultados terminales; esa garantía durable queda aplazada.

### 2.2 Búfer del gateway (almacenar y reenviar)

**Almacenar y reenviar (*store-and-forward*):** guardar mensajes mientras no
hay conexión y enviarlos cuando vuelve.

- Una trama sale del búfer solo cuando el broker devuelve su PUBACK.
- Orden de envío: el más antiguo primero (FIFO).
- Cada mensaje enviado desde el búfer lleva `buffered = true`.
- Medio (RAM o flash), capacidad y política al llenarse (descartar la más
  antigua o la más nueva): **pendientes** (P-IOT-10). Las capacidades reales del
  gateway no están medidas.
- Si el búfer está en RAM, un reinicio del gateway pierde las tramas pendientes.
  Debe documentarse como pérdida aceptada o resolverse con flash.
- Al reconectar, el vaciado debe limitar su ritmo para no saturar el broker
  (valor pendiente).

### 2.3 Deduplicación

- **Identidad persistente de una trama [Aprobado, IoT-04.1]:**
  `(coordinator_id, credential_id, boot_counter, seq)`. Sustituye a la identidad
  inicial (`coordinator_uid`, `boot_counter`, `seq`).
  - **Por qué incluye `credential_id`:** la reemisión (IoT-03.2-R2) reinicia la
    ventana anti-replay para que el coordinador vuelva a `boot_counter` 0. Sin la
    credencial, las primeras tramas legítimas tras reemitir, como (0, 0),
    colisionarían con tramas históricas del ciclo anterior y se descartarían como
    duplicados. Con `credential_id` cada credencial abre un espacio de identidad
    propio. No abre vía de reenvío: las tramas antiguas se firmaron con la clave
    revocada y no autentican.
  - **Se aplica con una restricción `UNIQUE`** sobre esas cuatro columnas en la
    tabla de tramas recibidas de MariaDB (se diseñará en la implementación; no
    se crean modelos ni migraciones en IoT-04.1). Reenvíos MQTT (QoS 1) y dos
    procesos concurrentes chocan con la restricción y se tratan como duplicado
    idempotente.
  - **Distinto de la ventana anti-replay:** la ventana (`CoordinatorBootWindow`)
    solo rechaza arranques antiguos y no guarda `seq`; la deduplicación detecta
    la misma trama dentro de un arranque. Ambos mecanismos coexisten.
- **Autoridad:** una restricción `UNIQUE` en MariaDB, no una comprobación
  previa en Python. Una consulta previa sola tiene condición de carrera: dos
  copias simultáneas podrían pasar la comprobación a la vez. (En la ingestión
  aprobada, la consulta previa es segura porque se hace con la finca y el
  coordinador bloqueados, §2.6.3; el `UNIQUE` sigue siendo la autoridad.)
- La trama y sus mediciones se insertan en **una sola transacción**: o se guarda
  la trama con todas sus mediciones válidas, o nada. Las lecturas de sensores
  desactivados no se guardan como mediciones, pero siguen en `raw_frame`
  (§2.6.5). Si la restricción única colisiona, se compara la identidad natural
  y el hash canónico reconstruido: solicitudes semánticamente idénticas son
  duplicados; diferencias se clasifican como conflicto sin sobrescribir la fila.
- Opciones de modelo (P-IOT-27):
  - **A.** Restricción única en la medición. Una sola tabla. *(Descartada.)*
  - **B.** Tabla de tramas recibidas con restricción única
    (`coordinator`, `credential`, `boot_counter`, `seq`) y mediciones como
    hijas. Permite guardar los metadatos de recepción una sola vez por trama.
  - **Resuelto en IoT-04.3-A: opción B** (§2.6), porque `lora_rx_at`, `rssi`,
    `snr`, `buffered` y `gateway` son de la trama, no de cada lectura, y evita
    repetirlos en cada fila.
- No se usa SQL específico (como `INSERT IGNORE`) fuera del ORM sin justificarlo:
  se captura `IntegrityError` dentro de un punto de guardado (*savepoint*).

### 2.4 Persistencia en MariaDB

- Motor InnoDB, `utf8mb4`, igual que el resto del proyecto
  (`config/database.py`).
- Cada fila IoT de telemetría lleva `farm_id`, desnormalizado (repetido a
  propósito): permite filtrar y crear índices por finca sin cruzar varias
  tablas.
- Índices para el historial: (`sensor`, `measured_at`) y (`farm`,
  `measured_at`) (IoT-04.3-7, §2.6.6). Sustituyen al índice previsto
  inicialmente (`farm`, `sensor`, `measured_at`).
- Los valores numéricos usan decimal fijo, no coma flotante, siguiendo
  `apps/inventario/models.py`. Precisión por tipo de métrica: P-IOT-28.
- Catálogo de tipos de métrica y unidades **en código**, como
  `apps/inventario/catalog.py`. Las métricas concretas dependen de los sensores
  confirmados (P-IOT-28).
- Retención y agregación de históricos: pendientes (P-IOT-29).
- Las mediciones **no** se registran como `AuditEvent`.

### 2.5 Clasificación de errores en el consumidor

- **Permanentes** (confirmar y descartar): JSON inválido, versión no
  soportada, campos no permitidos, gateway o coordinador desconocido o inactivo,
  finca inactiva, coordinador de otra finca, sin autorización vigente entre
  coordinador y gateway, firma inválida, canal sin sensor registrado, métrica
  incompatible, arranque fuera de la ventana, conflicto de identidad (se
  registra, §2.6.4), valor fuera de rango (rangos pendientes, P-IOT-28). Un
  sensor registrado pero desactivado **no** es un error: solo se omite su
  lectura (§2.6.5).
- **Duplicado idéntico:** se confirma como éxito sin escribir (§2.6.4).
- **Transitorios** (no confirmar, reintentar): base de datos no disponible,
  bloqueo agotado, conexión perdida.
- Si un error no se puede clasificar, se trata como transitorio con un número
  máximo de reintentos y luego como permanente con registro, para que un
  mensaje defectuoso no bloquee la cola (valor pendiente).
- Interbloqueo (MariaDB 1213) y espera de bloqueo agotada (1205): transitorios.
  Un `IntegrityError` que no sea la colisión de identidad no se trata como
  duplicado (§2.6.4).
- Proceso largo: cerrar conexiones de base de datos viejas entre mensajes
  (`django.db.close_old_connections`).

### 2.6 Modelo de persistencia e ingestión (implementado en el repositorio)

El modelo corresponde a las decisiones IoT-04.3-1 a IoT-04.3-9 en
[DEC-13 §16](../decisions/DEC-13-arquitectura-iot.md). La migración en una
instalación y la compatibilidad operativa de MariaDB no se verificaron en este
BUILD.

#### 2.6.1 Modelo lógico

**`ReceivedFrame`** (`iot_received_frames`): una fila por trama **autenticada y
aceptada** (IoT-04.3-2). Sin columna de estado; las tramas inválidas no se
guardan aquí (su cuarentena es P-IOT-19, pendiente).

| Campo | Contenido |
|---|---|
| `farm`, `gateway`, `coordinator`, `credential`, `authorization` | FK (PROTECT). `authorization` es la fila de `CoordinatorGateway` vigente al aceptar la trama (IoT-04.3-5, IoT-04.3-6) |
| `frame_version`, `key_version`, `boot_counter`, `seq`, `reading_count` | Cabecera de la trama, necesaria para la identidad, los índices y los CHECK |
| `raw_frame` | Bytes exactos recibidos (IoT-04.3-1); `signed_bytes` y `tag` son cortes de este campo |
| `gateway_received_at`, `gateway_clock_synced`, `buffered`, `rssi`, `snr` | Metadatos declarados por el gateway en el mensaje MQTT; no firmados por el coordinador |
| `server_received_at` | Recepción MQTT según el reloj del servidor |
| `measured_at`, `measured_at_approximate` | Hora estimada de captura (ver [flujo §2.3](iot-flujo-medicion.md)): la del gateway si declara reloj sincronizado; si no, la del servidor con la marca de aproximada. La tolerancia es P-IOT-11 (pendiente) |
| `created_at` | Persistencia |

**`Measurement`** (`iot_measurements`): una fila por lectura guardada de una
trama.

| Campo | Contenido |
|---|---|
| `frame` | FK obligatoria a `ReceivedFrame` |
| `farm`, `coordinator`, `measured_at` | Copias de la trama, garantizadas por FK compuesta (IoT-04.3-4) |
| `sensor`, `channel`, `metric` | Sensor activo del canal y su métrica del catálogo |
| `raw_value` | Entero recibido (int16 o uint16) |
| `value`, `is_invalid_marker` | Valor decimal (`raw_value / 100`) y marca de centinela; la política de centinelas está pendiente |

La unidad no se guarda: sale del catálogo.

#### 2.6.2 Integridad referencial (FK compuestas)

Siguen el patrón de `iot.0002` (SQL idempotente, `ON UPDATE RESTRICT ON DELETE
RESTRICT`); Django no las modela.

| FK | Destino | Garantiza | Requiere en el destino |
|---|---|---|---|
| `ReceivedFrame (coordinator_id, credential_id)` | `CoordinatorCredential (coordinator_id, id)` | La credencial es de ese coordinador | Nuevo `UNIQUE (coordinator, id)` |
| `ReceivedFrame (authorization_id, coordinator_id, gateway_id, farm_id)` | `CoordinatorGateway (id, coordinator_id, gateway_id, farm_id)` | Autorización, coordinador, gateway y finca coherentes; implica que gateway y coordinador son de la finca de la trama | Nuevo `UNIQUE (id, coordinator, gateway, farm)` |
| `Measurement (frame_id, coordinator_id, farm_id, measured_at)` | `ReceivedFrame (id, coordinator_id, farm_id, measured_at)` | Las copias de la medición coinciden con su trama | `UNIQUE` en la tabla nueva. Compatibilidad con DATETIME(6) por validar (IoT-04.3-B) |
| `Measurement (sensor_id, coordinator_id, channel, metric)` | `Sensor (id, coordinator_id, channel, metric)` | El sensor es de ese coordinador, canal y métrica | Nuevo `UNIQUE (id, coordinator, channel, metric)` |

Las FK `(gateway_id, farm_id)` y `(coordinator_id, farm_id)` de la trama no son
necesarias: las implica la FK de la autorización.

#### 2.6.3 Transacción de ingestión y bloqueos (IoT-04.3-3)

Antes de la transacción: decodificación (IoT-06) y `parse_lora_frame`; un error
estructural se rechaza sin tocar la base.

Dentro de `transaction.atomic()`, siempre con `SELECT … FOR UPDATE` y en este
orden:

1. **Farm** de la finca del gateway; debe estar activa. Se conserva hasta
   `COMMIT` o `ROLLBACK`.
2. **Coordinator** por uid **dentro de esa finca**; activo. Un coordinador de
   otra finca no se encuentra ni se bloquea.
3. **Gateway**, activo y de la misma finca.
4. **CoordinatorGateway** vigente del par. No se usa
   `lock_active_authorizations`: su JOIN bloquea filas fuera de este orden.
5. **CoordinatorCredential** activa; se descifra la clave y se verifica el HMAC
   sin soltar el bloqueo (cierra M-03 cuando se implemente).
6. **Deduplicación** por identidad (§2.6.4), antes de la ventana: un reenvío
   antiguo recibe respuesta de duplicado y no de "fuera de ventana".
7. **BootWindow** (`accept_coordinator_sequence`).
8. **Sensors** del coordinador; se aplica la política de §2.6.5.
9. Inserción de `ReceivedFrame` (en un *savepoint*) y de sus `Measurement`.
10. `COMMIT`. Solo después el consumidor envía el PUBACK (IoT-06).

**Semántica frente a la desactivación de la finca:** si la ingestión obtiene
primero el bloqueo de `Farm`, la desactivación espera a que termine; si la
desactivación obtiene primero el bloqueo y confirma la baja, la ingestión
posterior lee la finca inactiva y rechaza la trama. El mismo razonamiento vale
para revocar la autorización o la credencial y desactivar el coordinador, el
gateway o un sensor, porque todas esas operaciones bloquean antes la misma
`Farm`. Cualquier error deshace la transacción completa, incluida la ventana
antirreplay. QoS 1 entrega "al menos una vez"; la idempotencia la dan el
`UNIQUE` y el orden `COMMIT` → PUBACK, no MQTT.

#### 2.6.4 Duplicados y conflictos de identidad (IoT-04.3-9)

| Caso | Resultado |
|---|---|
| Misma identidad y mismo hash canónico (incluye `raw_frame` y metadatos) | Duplicado idempotente: éxito, sin escribir y sin tocar la ventana |
| Misma identidad y hash distinto, con `raw_frame` distinto | Conflicto: no se sobrescribe la trama original; se registra en `iot_frame_conflicts` y se rechaza como permanente |
| Misma identidad y hash distinto, con `raw_frame` igual | Conflicto de metadatos; se rechaza y registra en log. `FrameConflict` no representa esos metadatos y no se inserta una fila engañosa |
| Mismo `boot_counter`/`seq` con otra credencial | Identidad distinta, válida por diseño (reemisión) |
| `IntegrityError` en el *savepoint* | Si la identidad ya existe, es un duplicado; si no, se propaga como error interno, nunca como duplicado |

`iot_frame_conflicts` guarda la referencia a la trama aceptada original, los
bytes conflictivos, su SHA-256, el gateway y `server_received_at`, con un
`UNIQUE` (trama original, SHA-256) para no duplicar el mismo conflicto ante
reenvíos. Un conflicto solo puede producirlo quien tiene la clave (el HMAC ya es
válido): fallo de firmware (P-IOT-13) o clave comprometida. Nunca se registran
secretos, claves HMAC ni credenciales descifradas.

#### 2.6.5 Autorización histórica y política selectiva de sensores

- **Autorización (IoT-04.3-6):** se registra la autorización vigente al procesar
  y aceptar la trama. Una trama almacenada en el búfer y entregada después de una
  revocación se evalúa con la autorización vigente en ese momento; si no hay
  ninguna, se rechaza. No se infiere la autorización histórica a partir de
  `gateway_received_at`.
- **Sensores (IoT-04.3-8):**

| Situación del canal | Resultado |
|---|---|
| Sensor registrado, activo y con métrica coincidente | Se guarda la medición |
| Sensor registrado pero desactivado | Se acepta la trama, se conserva `raw_frame` y se omite solo esa medición |
| Canal sin sensor registrado | Se rechaza la trama entera |
| Métrica incompatible con el sensor | Se rechaza la trama entera |

No existe un estado adicional de medición: una lectura omitida no se guarda.
**Todos los sensores desactivados (IoT-04.3-10):** una trama autenticada y
autorizada puede aceptarse aunque todos sus sensores estén desactivados. Se
conserva `ReceivedFrame` y se crean cero filas `Measurement`. Esta situación no
es un error de persistencia.

#### 2.6.6 Índices (IoT-04.3-7)

- `Measurement (sensor, measured_at)`: historia y última lectura de un sensor.
  La API resuelve antes el sensor dentro de la finca del usuario; las FK
  compuestas garantizan que sus mediciones son de esa finca.
- `Measurement (farm, measured_at)`: línea temporal de la finca.
- Obligatorios: `ReceivedFrame UNIQUE (coordinator, credential, boot_counter,
  seq)`, `Measurement UNIQUE (frame, channel)` y los índices que exigen las FK
  compuestas de §2.6.2.
- Historia ordenada por (`measured_at`, `id`), con paginación por cursor; dentro
  de una credencial, el orden real de envío es (`boot_counter`, `seq`).

## 3. Explicación sencilla

Cada tramo tiene su propio "recibido". El último y el más importante es el de
MariaDB: hasta que no está guardado, nadie da el mensaje por entregado. Como
todos reintentan cuando dudan, a veces llegan copias. La base de datos tiene
una regla que no admite dos tramas con el mismo número del mismo coordinador:
la copia choca con esa regla y se ignora sin error.

## 4. Ejemplo práctico

El Wi-Fi de la finca cae 2 horas:

1. El coordinador sigue enviando `(C-A, 7, 120…180)`.
2. El gateway las guarda en el búfer.
3. Vuelve el Wi-Fi; el gateway publica desde la 120, cada una con
   `buffered = true`.
4. La 150 se publica, pero el PUBACK se pierde; el gateway la reenvía.
5. El consumidor guarda la 150 una vez; la copia choca con la restricción única
   y se confirma sin insertar.

## 5. Decisiones aprobadas

- MariaDB como única base de datos del módulo IoT.
- Confirmación al broker solo después del `COMMIT`.
- QoS 1 con deduplicación por (`coordinator_id`, `credential_id`,
  `boot_counter`, `seq`) (D-01, IoT-04.1; sustituye a la identidad inicial
  `(coordinator_uid, boot_counter, seq)`).
- Hash canónico de solicitud y persistencia de resultados terminales definidos
  por el contrato B0: no están integrados en el modelo actual y requieren una
  decisión/autorización de esquema.

## 6. Aspectos pendientes

- P-IOT-08 ACK LoRa.
- P-IOT-10 Medio, capacidad y política del búfer del gateway.
- ~~P-IOT-27 Forma de las tablas~~: resuelto en IoT-04.3-A (opción B, §2.6).
- P-IOT-11 Tolerancia del reloj del gateway.
- P-IOT-19 Cuarentena de tramas inválidas.
- P-IOT-28 Métricas, unidades, precisión, rangos físicos y política de
  centinelas.
- P-IOT-29 Retención y agregación; particionado futuro (MariaDB no admite FK en
  tablas particionadas).
- Máximo definitivo de lecturas por trama; tamaño máximo del mensaje MQTT y
  Base64; consumidor MQTT (IoT-06).
- Comparación de `key_version` con la generación de la credencial.
- Compatibilidad de la FK compuesta sobre DATETIME(6) en MariaDB 11.8, por
  validar en IoT-04.3-B.
- ~~Trama aceptada sin ninguna medición porque todos sus sensores están
  desactivados~~: resuelto en IoT-04.3-B (IoT-04.3-10, §2.6.5); se acepta con
  cero mediciones.

## 7. Riesgos y consideraciones

- Pérdida de tramas si el búfer es en RAM y el gateway se reinicia (R-IOT-05).
- Crecimiento continuo de la tabla de mediciones (R-IOT-08).
- `config/database.py` puede seleccionar Postgres si existe `DATABASE_URL`; IoT
  está aprobado solo para MariaDB y debe definirse cómo se impone (R-IOT-11,
  P-IOT-02).

## Primera recepción concurrente (BUILD-FLEPSI-02L)

**Causa demostrada (entorno desechable, MariaDB 11.8.8):** con 3 o más
receptores simultáneos del mismo mensaje, el flujo anterior devolvía
`PERSISTENCE_ERROR` con **errno 1213** (interbloqueo), en modo `off` y `record`.
Los perdedores del `INSERT` (1062) conservan el bloqueo S de la comprobación de
duplicados; pedir `select_for_update` (X) en la misma transacción formaba un
ciclo S->X entre ellos. No había pérdida ni duplicado (sin PUBACK; reentrega).

**Flujo actual** (`apps/iot/services/mqtt_delivery_receipts.py`):

1. **Sin transacción externa (BUILD-FLEPSI-02M).** `register_mqtt_delivery`
   gestiona sus propias transacciones. Si se invoca dentro de
   `transaction.atomic()` lanza `TransactionManagementError` antes de validar,
   consultar o escribir nada. No existe recuperación en línea ni reintento
   dentro de la transacción del llamador. Llamadores actuales: solo el
   consumidor MQTT, vía `register_encrypted_mqtt_delivery`, sin `atomic`;
   no hay `ATOMIC_REQUESTS`.
2. **Cada intento es una transacción propia** (`_register_attempt`).
3. **Colisión UNIQUE (1062).** La transacción del INSERT se abandona por
   completo (rollback; se liberan los bloqueos) y la recuperación es **una
   transacción independiente**: busca la fila ganadora (bloqueada en
   `record`), la clasifica como `REDELIVERY` y observa la identidad. Si tras
   el 1062 la fila no es visible -> `PERSISTENCE_ERROR`, sin bucle. Un
   `IntegrityError` distinto de 1062 -> `PERSISTENCE_ERROR`.
4. **Reintento solo para el errno 1213.** Como máximo **3 intentos para 1213**
   (el inicial más 2 reintentos), con esperas de **20 ms** y **100 ms**; cada
   reintento empieza **después del rollback completo** del anterior. 1205,
   2013, 1452, errores sin número y cualquier otro: sin reintento.
5. **Transacciones totales por llamada: hasta 4** según la secuencia de
   errores: 1 inicial + 1 de recuperación de colisión + hasta 2 reintentos por
   1213. La recuperación de colisión no consume intentos de 1213 y ocurre como
   mucho una vez.
6. **Agotados los intentos** -> `PERSISTENCE_ERROR`: el consumidor no envía
   PUBACK, se desconecta y la sesión persistente permite la reentrega.
7. El errno se obtiene del número del controlador (`args[0]`), nunca del
   texto; se registra en el log sin texto del controlador. `error_code` del
   resultado no cambia (nombre de la excepción).

El ACK sigue dependiendo solo de `PERSISTED`/`REDELIVERY`, devueltos después
del COMMIT.
