# B2.1-03 — Contrato de entrega MQTT durable

- **Estado:** contrato consolidado de diseño, pendiente de revisión y verificación técnica antes de BUILD-01.
- **Normativa de origen:** `B2.1-03-CONTRACT-01` y `B2.1-03-DESIGN-01`, originales recibidos como adjuntos del propietario; no se encontraban en el repositorio.
- **Decisiones:** D-01 a D-21 comunicadas por el propietario para esta consolidación.
- **Fuentes del repositorio:** DEC-13 y documentos IoT relacionados, más `AGENTS.md`.
- **Fecha:** 2026-11-15.

La numeración consolidada D-01 a D-21 sigue la excepción documental del propietario. En `B2.1-03-CONTRACT-01` original los números D-01 a D-07 tenían otra asignación (ACK, reintentos, errores operativos, orden, payload, DLQ y alertas); sus requisitos se conservan, pero no se deben confundir los IDs originales con la numeración consolidada.

## 1. Alcance y garantías

Este contrato describe la futura bandeja durable MQTT y sus responsabilidades. No declara que esté implementada ni verificada.

Se mantienen invariantes: MariaDB/InnoDB como fuente durable; un worker Django inicial; `apps/iot/services/ingestion.py` como único escritor de mediciones aceptadas; `ReceivedFrame`, `Measurement` y `FrameConflict` como historial protegido; identidad de ingesta y deduplicación existentes; Frame v1, HMAC, anti-replay y autorización actual sin cambios; triggers append-only B1 sin cambios. Las tablas de entrega son operativas e independientes.

Garantía objetivo: ningún mensaje se confirma al broker antes de que exista una responsabilidad durable recuperable o un resultado final durable. La base de datos y MQTT no comparten una transacción distribuida; la semántica es al menos una vez con reconciliación idempotente.

## 2. Entidades conceptuales

### 2.1 `iot_mqtt_deliveries`

Una fila representa una unidad de transporte MQTT y su ciclo de vida local. Campos propuestos (tipos indicativos; precisión/tamaño final dependen de inspección de modelos y MariaDB):

| Campo | Tipo conceptual | Reglas |
|---|---|---|
| `id` | UUID/CHAR(36) o BINARY(16) | PK durable, independiente del MQTT `mid`. Elegir representación consistente con el proyecto. |
| `transport_key` | BINARY(32) | SHA-256 de representación canónica separada por dominio de topic y payload exactos; UNIQUE. Retener mientras exista la entrega. La fórmula precisa debe aprobarse antes de implementar. |
| `topic` | VARCHAR acotado | Topic recibido; longitud máxima configurable y validada. No confiar en identidad del cuerpo. |
| `payload_ciphertext` | BLOB/VARBINARY acotado | Sobre original cifrado; límite de bytes aplicado antes de persistir. Nunca en logs. Puede ser nulo solo para caso sobredimensionado si se define registro de metadatos sin payload. |
| `payload_nonce`, `payload_tag` | BINARY (12, 16 bytes recomendado) | Parámetros AES-256-GCM; nonce único por cifrado. Formato final y soporte de librería por verificar. |
| `payload_key_version` | VARCHAR/entero no secreto | Identificador de versión para resolver clave externa. No guardar material de clave. |
| `payload_sha256` | BINARY(32) | Huella del payload original para integridad/reconciliación; no sustituye al cifrado ni a `transport_key`. |
| `payload_size` | entero sin signo | Tamaño original validado; no aceptar límites ilimitados. |
| `qos` | entero pequeño | Debe ser QoS 1 para flujo cubierto por este contrato. |
| `received_at` | DATETIME(6) | Hora durable del servidor para FIFO, no hora declarada por gateway. |
| `coordinator_id` | FK nullable | Referencia candidata/identificada; null si no se puede identificar. No afirma autenticación hasta validar en ingesta. |
| `state` | enum/string limitado | Estado de la máquina §4. Evitar ENUM irreversible si las convenciones del proyecto aconsejan choices y migraciones incrementales. |
| `result_category`, `result_code` | string limitado nullable | Resultado sanitizado; separar resultado de transporte del de ingesta. |
| `reason_code` | string nullable | Categoría de rechazo/conflicto sin secretos ni payload. |
| `attempt_count` | entero ≥ 0 | Número de intentos de procesamiento iniciados; máximo automático cuatro. |
| `next_attempt_at` | DATETIME(6) nullable | Agenda durable para `RETRY_PENDING`. |
| `lease_owner` | identificador opaco nullable | Worker que posee lease; no identidad del usuario. |
| `lease_token` | UUID nullable | Token de fencing nuevo en cada reclamación. |
| `lease_expires_at` | DATETIME(6) nullable | Inicio 60 s; renovable bajo reglas verificadas. |
| `last_error_code`, `last_error_safe` | campos acotados nullable | Error sanitizado, sin traceback con secretos, payload, claves o datos sensibles. |
| `received_frame_id`, `frame_conflict_id` | FK nullable | Vínculos de resultado cuando existan; nunca permitir que la bandeja modifique filas históricas. Revisar FK/retención y nombres reales antes de BUILD. |
| `created_at`, `updated_at` | DATETIME(6) | Fechas de gestión; cualquier cambio de estado deja evento append-only. |
| `resolved_at`, `resolved_by`, `resolution_code` | nullable | Resolución formal auditada; actor y motivo obligatorio para intervención manual. |

Restricciones propuestas: PK; UNIQUE en `transport_key`; CHECK/validación de `attempt_count >= 0`, QoS dentro de permitido, consistencia entre lease y estado, tamaño acotado. Índices para (state, next_attempt_at, received_at), lease vencida, (coordinator_id, received_at, id) y fecha de retención/resolución. Verificar soporte y estrategia de constraints/índices en MariaDB 11.8.8. No imponer UNIQUE sobre identidad LoRa/ingesta en esta tabla: la misma identidad con payload distinto debe poder llegar a clasificación de conflicto.

### 2.2 `iot_mqtt_delivery_events`

Historial append-only, una fila por recepción/decisión/transición/acción administrativa. Campos propuestos: `id`, `delivery_id` FK, `occurred_at`, `event_type`, `from_state`, `to_state`, `attempt_number`, `actor_id` nullable, `worker_id` nullable, `lease_token` nullable, `result_code`, `safe_detail` acotado, `correlation_id` opcional. Sin payload ni secretos. Índice por (`delivery_id`, `occurred_at`, `id`) y por tipo/fecha para observabilidad. Una transición y su evento se escriben en la misma transacción que cambia la entrega.

No declarar protección append-only ya instalada: mecanismo de permisos/triggers de eventos es pendiente. En todo caso, eventos de entrega no modifican triggers ni historial B1.

### 2.3 Relaciones e identidad

`transport_key` representa el sobre exacto (topic canónico + bytes exactos payload), no la identidad MQTT `mid`, ni la identidad LoRa, ni identidad de negocio. Un redelivery idéntico se vincula a la misma fila por UNIQUE. Dos sobres diferentes que contengan igual identidad LoRa permanecen diferenciados para que `ingestion.py` decida duplicado o conflicto. No añadir unicidad que suprima un conflicto.

La canonicidad debe fijar si se usan bytes UTF-8 exactos del topic entregado por cliente o una forma normalizada, separador de dominio y encoding inequívoco de longitudes. Una colisión SHA-256 criptográfica se considera riesgo residual; los bytes/topic/hash se verifican al encontrar UNIQUE y una incompatibilidad se registra como incidente, sin sobrescribir. Un sobre intencionalmente republicado con topic/payload idénticos no se puede distinguir de una redelivery sin un identificador del productor; esto queda como limitación explícita.

FK opcionales hacia `ReceivedFrame`/`FrameConflict` son referencias de lectura. Las entidades de historial pueden retenerse indefinidamente y la bandeja purgarse; por ello se debe elegir SET NULL/RESTRICT o tabla de vínculo sin violar historial ni impedir purga. No fijar acción FK hasta verificar restricciones actuales y política de retención. No incluir claves HMAC ni secretos en ninguna entidad.

## 3. Estados y transiciones

| Estado | Evidencia/entrada | Salida, reintento y ACK |
|---|---|---|
| `RECEIVED` | Fila, clave de transporte única, ciphertext/huella y evento de recepción confirmados en MariaDB. | Se reclama a `PROCESSING`. **No ACK por este estado aislado.** |
| `PROCESSING` | Lease vigente, owner/token de fencing y evento de reclamación. | A resultado, `RETRY_PENDING`, `DEAD_LETTER` o pausa operativa. Recuperar tras lease vencido. No ACK solo por procesar. |
| `ACCEPTED` | Ingesta y estado de bandeja durablemente confirmados, referencia histórica si disponible. | Resultado terminal; ACK elegible tras commit. |
| `DUPLICATE` | Ingesta identificó misma identidad y contenido como aceptación previa. | Resultado durable ligado a la fila aceptada; ACK elegible tras commit. |
| `RETRY_PENDING` | Error transitorio, contador/próxima ejecución y evento durables. | Reprocesar cuando venza agenda y se obtenga lease. ACK elegible solo tras persistir la responsabilidad completa. |
| `REJECTED` | Rechazo de negocio clasificado y evidencia durable; no equivaler a mero log. | Terminal hasta resolución/redrive autorizado según política. ACK elegible tras commit. |
| `CONFLICT` | Conflicto durable con vínculo/evidencia; conservar original, no sobrescribir. | Terminal hasta resolución/redrive autorizado. ACK elegible tras commit. |
| `DEAD_LETTER` | Entrega durable que requiere operación/manual, causa, intentos y payload conservado. | Permanece hasta resolución formal; redrive auditado. Si ACK se permite al entrar, requiere aprobación explícita del punto abierto §11. |
| `RESOLVED` | Acción/resolución durable con actor, fecha, motivo y evento. | Estado de cierre; empieza retención de 30 días. No cambia mediciones/historial. |

Transiciones permitidas: `RECEIVED→PROCESSING`; `RETRY_PENDING→PROCESSING`; `PROCESSING→ACCEPTED|DUPLICATE|REJECTED|CONFLICT|RETRY_PENDING|DEAD_LETTER`; `PROCESSING→RECEIVED` solo por rollback que no dejó cambio durable; `PROCESSING→PROCESSING` por recuperación de lease vencido con nuevo fencing token; `DEAD_LETTER→PROCESSING` solo redrive auditado; `DEAD_LETTER|REJECTED|CONFLICT→RESOLVED` con resolución auditada. La política de cierre para ACCEPTED/DUPLICATE y su purga debe fijarse antes de implementación. Toda transición actualiza fila y agrega evento atómicamente.

Error desconocido/sistémico: pausar y alertar, sin clasificar como rechazo ni mandar automáticamente a DLQ. `OPERATIONAL_HOLD` sería una etiqueta/estado adicional posible, no aprobado en la lista; antes de BUILD hay que decidir si se usa y si un hold durable habilita ACK. Hasta entonces, no ACK por error operativo no resuelto.

## 4. Matriz de ACK

`ACK elegible` significa que el consumidor puede invocar el ACK manual después de commit; no que broker haya confirmado el ACK. El ACK local exitoso no se persiste como confirmación efectiva. Nunca usar el `mid` como identidad durable.

| Resultado/condición | ¿ACK? | Precondición durable |
|---|---|---|
| Sobre recibido y `RECEIVED` solamente | No | La fila preserva el sobre, pero todavía no hay resultado ni agenda recuperable que cubra todas las fallas. |
| `ACCEPTED` | Sí | Commit de ingesta y estado final visibles en MariaDB. |
| `DUPLICATE` previamente aceptado | Sí | Identidad/contenido comparados y aceptación previa confirmada. |
| `REJECTED` | Sí | Rechazo, categoría y evidencia durable; no basta `AppError` en memoria/log. |
| `CONFLICT` | Sí | Conflicto durable y referencia/evidencia suficiente; original intacto. |
| Error transitorio + `RETRY_PENDING` | Sí | Contador, causa segura, fecha próxima y payload descifrable/retenido durables; MariaDB asume recuperación. |
| Error transitorio con DB caída o escritura fallida | No | No existe responsabilidad durable nueva. |
| Error operativo/desconocido | No hasta decisión | Pausar y alertar; no convertir a rechazo ni DLQ automático. La regla para hold durable queda pendiente. |
| `DEAD_LETTER` | Pendiente de aprobación expresa | D-10 establece retención hasta resolución, pero D-01 no dice literalmente si la transferencia durable a DLQ basta para ACK. |
| Fallo/crash tras commit y antes de ACK | Redelivery permitido | Resolver por `transport_key`; no repetir mediciones gracias a idempotencia de ingesta. |
| Error o resultado ambiguo de `client.ack()` | No marcar confirmado | MQTT puede reentregar; reconocer redelivery de forma idempotente. |

## 5. Contrato transaccional y atomicidad

El objetivo D-18 es una misma conexión MariaDB y transacción exterior para la bandeja y la ingesta aceptada; bloques internos pueden ser savepoints. `transaction.atomic()` anidado solo crea savepoint si usa la misma conexión/alias y backend, pero eso no está verificado aquí. No asumir atomicidad por similitud de APIs ni entre MariaDB y MQTT.

### A. Recepción durable

1. Recibir topic/payload; imponer límite antes de materializar/copiar a almacenamiento. QoS esperado 1.
2. Calcular `transport_key`, parsear solo lo necesario para metadata no autenticada y cifrar payload usando clave externa.
3. En transacción, insertar delivery y `RECEIVED` event. En colisión UNIQUE, comparar topic, bytes descifrados/hash y tamaño: equivalente = redelivery a fila existente; distinto/inconsistente = incidente, nunca sobrescribir.
4. Commit. Aún no ACK mientras esté solo RECEIVED; solicitar procesamiento o continuar flujo en consumidor según mecanismo finalmente aprobado.

### B. Reclamo por worker

Reclamar una fila elegible por lock atómico/lease, generar fencing token, marcar `PROCESSING`, incrementar el contador cuando el intento de procesamiento realmente comienza y escribir evento en la misma transacción. Confirmar compatibilidad de select-for-update/skip-locked, aislamiento, índices, deadlocks y límites MariaDB antes de usar API específica. El consumidor no debe perder control de memoria: límite en vuelo configurable y control de recepción cuando worker/DB se detienen.

### C. Procesamiento e ingesta

1. Verificar owner/token/lease vigente y recuperar/descifrar payload; si falta clave, preservar fila, pausar/alertar, no descartar.
2. Invocar la ruta existente del servicio de ingesta; no duplicar validación ni persistencia de mediciones.
3. Si accepted/duplicate/reject/conflict, registrar estado y evento junto con la ingesta dentro de la misma transacción exterior y conexión, usando savepoints internos solo si se confirma el comportamiento.
4. Si transitorio, rollback de escrituras de ingesta fallidas y persistir `RETRY_PENDING`, backoff y evento en la transacción de recuperación. Si el mismo error hace imposible persistir, no ACK y permitir recuperación por redelivery/DB.
5. Commit DB; solo después invocar ACK cuando matriz lo permite.

### D. Commit incierto/recuperación

Crash antes del commit común: bandeja e ingesta revierten; nueva entrega se procesa. Resultado de commit incierto: redelivery/consulta por `transport_key`; verificar si el estado final está durable. Si el historial aceptado existe pero falta estado final por una divergencia de transacción o despliegue anterior, reconciliar con la identidad/deduplicación de `ingestion.py`; no insertar otra medición. D-17 obliga a esta recuperación idempotente, aunque D-18 busca que el caso no ocurra en la ruta normal. Cualquier separación en dos commits rompe la garantía de atomicidad y requiere diseño/aceptación específica.

ACK ocurre fuera de la transacción. No hay forma de garantizar exactamente una vez entre commit MariaDB y PUBACK; QoS 1/redelivery más idempotencia durable son el mecanismo.

## 6. Reintentos, lease y orden

Máximo cuatro intentos totales (inicial + tres adicionales). Espera base de 1 s, backoff exponencial, techo 30 s, jitter ±20 %. Persistir `attempt_count` y `next_attempt_at`; timers locales no son autoridad. Errores transitorios aprobados: conexión MariaDB, timeout, deadlock y lock wait timeout. No clasificar errores de programación/configuración como transitorios infinitos ni como negocio.

Tras agotamiento, preservar entrega y mover a `DEAD_LETTER` solo si la causa es elegible y la transferencia de responsabilidad queda durable. Errores sistémicos desconocidos provocan pausa/diagnóstico, no DLQ automática. La semántica de ACK al entrar en DLQ permanece abierta.

Lease inicial 60 s. Renovación requiere owner/token actual y lease no vencida; recuperación solo tras comprobar expiración. Un worker viejo no puede completar ni cambiar estado después de perder propiedad (fencing). Acordar reloj de DB vs aplicación, renovación, timeout de procesamiento y recuperación en carreras.

FIFO por coordinador usa el orden durable de recepción (`received_at`, luego PK) como secuencia de servicio. Mantener la barrera hasta un resultado que no pueda alterar anti-replay; retry/DLQ sin resolver bloquea ese coordinador, no los demás. Operaciones deben ofrecer pausa/redrive/resolución auditada para desbloquearlo. Mensajes sin coordinador quedan en carril aislado sin bloquear otros. Verificar si una sola partición de cola/resolución basta para mensajes de coordinador aún no autenticados.

Pausa por coordinador ante entrega bloqueada; pausa global por fallos sistémicos. En pausa global no iniciar procesamiento nuevo; conservar lo persistido; no ACK mensajes nuevos sin responsabilidad durable; al reanudar respetar FIFO por coordinador. Paho/Mosquitto deben limitar mensajes en vuelo y la recepción debe frenarse según capacidad; validar comportamiento real.

## 7. DLQ, permisos y operaciones

La DLQ es un estado/vista durable de la bandeja, no un broker separado. Debe mostrar causa codificada, último error sanitizado, intentos, edad, actor/acciones y acceso restringido al payload cifrado. No incluir secretos, HMAC, credenciales descifradas ni payload en logs.

Permisos reutilizan el sistema de autorización existente y se mantienen separados: lectura de metadatos, inspección/descifrado, redrive y resolución manual. Cada acción requiere actor, timestamp, motivo y evento append-only. El alcance exacto por rol/capacidad queda pendiente de revisar contra el sistema real antes de BUILD. Redrive reabre el procesamiento de la misma delivery con nuevo evento, sin borrar intentos previos ni modificar historial B1.

Resolución formal no significa corrección de mediciones. Cualquier corrección de telemetría sigue fuera de B2.1-03. Mensajes inválidos no bloquean coordinadores independientes; bloqueo por coordinador se hace visible y operable.

## 8. Cifrado, retención y claves

AES-256-GCM para payload original. Guardar ciphertext, nonce, tag, versión no secreta, tamaño y hash. Claves fuera del repositorio y MariaDB, con versionado/rotación. Conservar versiones históricas mientras haya payload que dependa de ellas. Si la clave no está disponible, no descartar ni dejar que purga automática avance; pausar y alertar. Backups protegidos y recuperación probada son requisitos aprobados, pero mecanismo/custodia no están definidos.

Retención: pendientes y `DEAD_LETTER` sin resolver no se eliminan. Tras resolución formal, retener entrega, `transport_key`, payload y eventos 30 días y purgar de modo controlado/auditable. Rechazados/resueltos siguen el mismo inicio de retención; formalizar compatibilidad con todo estado final. Purgar ciphertext y borrar key material local/cache de forma segura según mecanismo futuro; no eliminar claves históricas requeridas por otras filas. No tocar las tablas históricas ni sus triggers.

## 9. Observabilidad y umbrales

Contadores/gauges/edad: entregas por estado, pendiente más antiguo, reintentos, leases vencidas, errores de persistencia, fallos de ACK, DLQ, worker detenido/pausado. Alertas iniciales configurables:

- `RECEIVED > 100`.
- Antigüedad de `RECEIVED` > 5 minutos.
- `RETRY_PENDING > 20`.
- Leases vencidas > 5.
- Tres fallos consecutivos de persistencia.
- Pausa global y fallos de ACK: alerta inmediata.

Todos los umbrales son configuración y requieren calibración operativa. Evitar métricas con payload, claves, `transport_key` completa o identificadores personales como dimensiones.

## 10. Criterios de aceptación del diseño

Antes de declarar listo BUILD-01, el diseño de implementación deberá demostrar en ambiente aislado:

1. No ACK por RECEIVED solo, DB caída, fallo persistencia o error operativo desconocido.
2. ACK posterior al commit para ACCEPTED, DUPLICATE, REJECTED, CONFLICT y `RETRY_PENDING`; decisión explícita para DEAD_LETTER/hold.
3. Redelivery tras commit/ACK ambiguo no duplica mediciones ni borra conflicto.
4. `transport_key` diferencia sobres distintos; misma clave y bytes equivalentes se idempotentizan; discrepancia ante colisión no sobrescribe.
5. Cuatro intentos como máximo, agenda durable, lease/fencing y recuperación de lease vencida.
6. Barrera bloquea solo coordinador afectado; mensaje sin coordinador no bloquea coordinadores.
7. Pausa global no inicia trabajos nuevos; al reanudar conserva FIFO.
8. Payload cifrado, clave externa/versionada; clave ausente conserva datos y genera alerta.
9. Inspección/redrive/resolución con permisos separados y eventos auditables.
10. Retención no purga pendiente/DLQ no resuelta; purga tras resolución + 30 días es controlada.
11. Migración incremental mantiene todas las protecciones append-only B1 y funciona tras instalación parcial en MariaDB 11.8.8.
12. Métricas/alertas y límite de vuelo/recepción se comportan conforme a configuración.

No se ejecutaron estos criterios en este trabajo.

## 11. Dependencias y asuntos a resolver antes de BUILD-01

1. Verificar técnicamente atomicidad sobre la misma conexión/base y savepoints entre bandeja e `ingestion.py`, incluidas excepciones y commit incierto.
2. Confirmar constraints/triggers B1, tabla/relaciones reales de `ReceivedFrame` y `FrameConflict`, y política FK de retención.
3. Cerrar definición canónica de `transport_key` y ambigüedad de publicaciones byte-idénticas intencionales.
4. Aprobar regla de ACK de `DEAD_LETTER` y hold operativo, y forma de responsabilidad manual durable.
5. Definir destino tras cuarto intento, redrive y desbloqueo formal del coordinador.
6. Fijar transiciones a `RESOLVED` para cada terminal y punto de inicio de retención.
7. Validar MariaDB 11.8.8/InnoDB y Django 5.2: locks, skip-locked, aislamiento, savepoints, índices, FKs, CHECK, DDL parcial/no atómico.
8. Verificar Paho/Mosquitto y cliente/configuración efectiva: manual ACK, sesión persistente, mensaje en vuelo, pausa/recepción, ACK ambiguo.
9. Definir custodia/versionado/rotación AES-GCM, backup/restauración, permisos y auditoría de acceso payload.
10. Definir si mensajes sobredimensionados tienen registro solo de metadata, límite y retención.
11. Calibrar umbrales, alertas, cardinalidad y operación Windows del worker.
12. Revisar permisos existentes y aprobar roles separados para inspección, redrive y resolución.

## 12. Plan futuro de BUILD

Secuencia conceptual compatible con el CONTRACT-01; rutas concretas y autorizaciones se definirán en cada BUILD:

| BUILD | Entrega | Pruebas/aceptación y riesgos principales |
|---|---|---|
| 1. Modelos y persistencia | Entidades/índices/constraints y migración incremental. | MariaDB aislada; instalación parcial; proteger triggers B1. |
| 2. Recepción durable | Clave transporte, inserción idempotente y transiciones de entrada/ACK. | Redelivery, colisión y DB no disponible; Paho manual ACK. |
| 3. Integración transaccional | Bandeja + `ingestion.py` en conexión/transacción común. | Savepoints, rollback, conflicto y commit incierto sin mediciones duplicadas. |
| 4. Worker y recuperación | Lease/fencing, FIFO por coordinador, pausa global/local. | Carrera de workers y lease expirado; compatibilidad de locks. |
| 5. Reintentos | Backoff/agenda persistidos y límite de cuatro intentos. | Reinicio durante espera y tipos de error. |
| 6. DLQ | Inspección/redrive/resolución con permisos/eventos. | No alterar B1; estado y ACK de DLQ aprobados. |
| 7. Seguridad/observabilidad | AES-GCM, permisos, retención, purga, métricas y alertas. | Clave ausente, backup/restauración y acceso auditado. |
| 8. Integración aislada | Validación extremo a extremo en entorno autorizado desechable. | Broker/MariaDB, flujo en vuelo, cortes, ACK y recuperación. |

Cada BUILD requerirá autorización expresa, alcance de archivos definido, pruebas aisladas demostradas y prohibición de acceder a bases protegidas/servicios no autorizados. Este contrato no inicia BUILD-01.

## 13. Conflictos y estado documental

- **Confirmación de permanentes:** el texto de persistencia IoT-01 §2.5 dice “confirmar y descartar”; el contrato MQTT vigente y DEC-13 T13 especifican que rechazos/conflictos no reciben PUBACK sin registro durable y marcan aquella regla como histórica/supersedida por B2.1-02. Para B2.1-03 prevalece la responsabilidad durable indicada por los originales y decisiones actuales. Los documentos antiguos no se modificaron.
- **Huella/deduplicación:** DEC-13 y persistencia dicen que el hash/huella está en memoria y que no todos los resultados se persisten. Este contrato introduce bandeja y `transport_key` como diseño futuro, no como hecho existente; no reemplaza identidad LoRa ni deduplicación de ingesta.
- **Atomicidad:** D-18 ordena transacción común/same connection; D-17 exige recuperación de aceptado con estado incierto. Se tratan como capas complementarias: atomicidad evita la separación normal; reconciliación idempotente cubre incertidumbre/legacy. La compatibilidad técnica sigue sin verificar.
- **DLQ/ACK:** conservación hasta resolución está aprobada, pero el ACK al pasar a `DEAD_LETTER` no está expresamente resuelto por D-01; queda abierto y no se inventa.
- **Errores operativos:** D-03/D-12 excluyen conversión en rechazo y DLQ indiscriminada; falta nombre de estado y regla de ACK del hold, explícitamente pendientes.

