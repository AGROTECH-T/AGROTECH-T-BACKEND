# DEC-13 — Arquitectura y contratos técnicos del módulo IoT

- **Identificador:** DEC-13
- **Título:** Arquitectura y contratos técnicos del módulo IoT
- **Estado de decisión:** Aprobada (parcial: los puntos de §6 siguen pendientes)
- **Estado de implementación:** Parcial. IoT-02.1: catálogo, identificadores y
  exigencia de MariaDB (§10). IoT-02.2: modelos, FK compuestas, servicios,
  capacidades y auditoría (§11). IoT-03.1: credenciales de gateway (§12).
  IoT-03.2: credenciales de coordinador (§13). IoT-04.2: parser binario LoRa v1
  y corrección M-02 (§15). IoT-04.3-C1: generaciones persistentes de credencial
  (§17). El código de modelos/migraciones, ingesta Django, MQTT y API REST se
  añadió posteriormente. El estado conectado a bases o broker no se verifica
  aquí. La implementación vigente usa Django como único escritor de ingesta.
- **Alcance:** BUILD IoT-01, IoT-02.1, IoT-02.2, IoT-03.1, IoT-03.2, IoT-04.2, IoT-04.3-C1; diseño IoT-04.1 (§14, en curso) e IoT-04.3-A (§16)
- **Fecha:** 2026-11-12 (actualizado el 2026-11-13, IoT-04.2, IoT-04.3-A y C1)
- **Relación:** DEC-01 (desactivación lógica de fincas), DEC-02 (roles por
  membresía), DEC-11 (capacidades por finca)

## 1. Propósito

### Arquitectura vigente — BUILD B0-ARCH-02 (actualiza B0-ARCH-01)

La decisión de implementación vigente es la Opción A: Django valida y procesa
la ingesta; `apps/iot/services/ingestion.py` es el único responsable de las
escrituras de ingesta en MariaDB, y el consumidor MQTT delega en ese servicio.
El Writer independiente, HTTPS/mTLS y la comunicación Django → Writer quedan
aplazados. `apps/iot/ingestion_identity.py` contiene helpers de identidad/hash
B0 integrados en la ingesta Django; esto no implementa Writer ni comunicación
entre servicios. El UUIDv5 se usa como correlación y la huella SHA-256 en memoria
para comparar duplicados con los campos almacenados. Ni la huella ni todos los
resultados terminales se persisten; esa garantía durable queda aplazada. Las
secciones históricas siguientes conservan las decisiones y estados que tenían
al redactarse; donde difieran, prevalece esta arquitectura vigente.

Registrar las decisiones técnicas aprobadas para IoT, las que se derivan de
ellas en esta etapa, los aspectos que siguen abiertos y los riesgos conocidos.
El detalle técnico está en los documentos de módulo enlazados en §8.

## 2. Contexto

- `apps/iot/` existe como esqueleto vacío registrado en `INSTALLED_APPS`.
- No hay galpón en `apps/production/avicultura/`.
- No existen dependencias MQTT ni procesos en segundo plano. El `Dockerfile`
  solo ejecuta `migrate` y gunicorn.
- El aislamiento por finca y la autorización actuales asumen un usuario humano
  (`apps/farms/services.py`, `apps/inventario/services/membership.py`).
- Hardware previsto: ESP32 coordinador con sensores cableados (uno o dos
  grupos), ESP32 gateway, módulos LoRa SX1278 en 433 MHz.

## 3. Decisiones aprobadas por el responsable del proyecto

1. Cadena: Sensores → coordinador ESP32 → LoRa → gateway ESP32 → Wi-Fi → MQTT →
   Mosquitto → Django → MariaDB → React.
2. LoRa con SX1278 en 433 MHz.
3. **HMAC-SHA256 con clave propia por coordinador** para autenticar tramas LoRa.
4. **MariaDB exclusivamente** para el módulo IoT.
5. **Eclipse Mosquitto** como broker MQTT.
6. React continúa como aplicación web; **no se instala MQTT** en los equipos de
   los usuarios.
7. Primera versión con sensores cableados directamente al coordinador.

## 4. Decisiones técnicas definidas en IoT-01

Se derivan de las anteriores y del código existente. Pueden revisarse antes de
implementarse, pero son la base de IoT-02.

| # | Decisión | Detalle |
|---|---|---|
| T1 | La finca se deriva del gateway registrado, nunca del mensaje | [seguridad §2.4](../modules/iot-seguridad.md) |
| T2 | El coordinador debe pertenecer a la misma finca que el gateway que entrega su trama | [seguridad §2.4](../modules/iot-seguridad.md) |
| T3 | Identidad de trama (`coordinator_uid`, `boot_counter`, `seq`); `boot_counter` persistente en NVS, `seq` en RAM. **Identidad persistente sustituida en IoT-04.1** por `(coordinator_id, credential_id, boot_counter, seq)` (§14) | [contrato LoRa §2.4](../modules/iot-contrato-lora.md) |
| T4 | La firma HMAC cubre los bytes exactos de la trama con un contexto fijo; el gateway los reenvía sin modificar (Base64) | [contrato LoRa §2.3](../modules/iot-contrato-lora.md) |
| T5 | La verificación HMAC la hace el backend; el gateway es un relé | [arquitectura §3.2](../modules/iot-arquitectura.md) |
| T6 | Topics `agrotech/v1/gw/{gateway_uid}/up` y `/status`; ACL por gateway; el cuerpo no lleva `farm_id` ni `gateway_uid` | [contrato MQTT §2.1](../modules/iot-contrato-mqtt.md) |
| T7 | QoS 1, sesión persistente del consumidor y PUBACK manual después del `COMMIT` | [contrato MQTT §2.3](../modules/iot-contrato-mqtt.md) |
| T8 | Deduplicación por restricción `UNIQUE` en MariaDB sobre `(coordinator_id, credential_id, boot_counter, seq)` (IoT-04.1); inserción atómica por trama | [persistencia §2.3](../modules/iot-persistencia-entrega.md) |
| T9 | Claves HMAC cifradas en reposo con clave maestra dedicada (no hash) | [seguridad §2.2](../modules/iot-seguridad.md) |
| T10 | Consumidor como proceso independiente de gunicorn | [arquitectura §3.2](../modules/iot-arquitectura.md) |
| T11 | IoT depende solo de `farms`; avicultura referenciará a IoT con asignaciones con vigencia | [arquitectura §3.6](../modules/iot-arquitectura.md) |
| T12 | Las mediciones no son `AuditEvent`; sí se auditan altas, rotaciones y revocaciones | [arquitectura §3.4](../modules/iot-arquitectura.md) |
| T13 | **Decisión histórica, supersedida por B2.1-02:** la regla inicial confirmaba y descartaba rechazos permanentes. La regla vigente no confirma rechazos ni conflictos hasta contar con persistencia durable; los errores transitorios y operativos tampoco se confirman. | [contrato MQTT §2.5](../modules/iot-contrato-mqtt.md) |

## 5. Consecuencias

- IoT-02 puede definir modelos de gateway, coordinador y sensor sin depender del
  formato binario de la trama.
- Se necesitará una dependencia cliente MQTT en IoT-05 (requiere autorización).
- Se necesitará una nueva clave maestra de entorno y su validación al arranque.
- Despliegue: un servicio adicional (consumidor) y Mosquitto.
- Agregar eventos IoT a `EventType` exigirá una migración `AlterField` en
  `apps/trazabilidad`, como las migraciones 0002 a 0008.

## 6. Registro de pendientes

| ID | Pendiente | Bloquea |
|---|---|---|
| P-IOT-01 | Ubicación del broker y del backend (en la finca o en la nube) | IoT-06 |
| P-IOT-02 | ~~Cómo imponer "solo MariaDB"~~ **Resuelto en IoT-02.1:** `apps/iot/db_guard.require_mariadb` en los servicios IoT, sin cambiar la configuración global (§10) | — |
| P-IOT-03 | ~~Formato de identificadores~~ **Resuelto en IoT-02.1:** `GW-`, `CO-`, `SN-` + 10 caracteres (§10) | — |
| P-IOT-04 | Significado de "grupo de sensores". **IoT-04.1:** no viaja en la trama v1 | — |
| P-IOT-05 | Parámetros de radio y cumplimiento normativo en Colombia | Firmware |
| P-IOT-06 | Estructura binaria de la trama, tamaño máximo y codificación de valores. **Parcial en IoT-04.1** (§14): cabecera, big-endian, uid, contadores, escalas y contexto aprobados; bloque de lectura de 5 bytes aprobado (P-05); pendientes el rango de temperatura, el tratamiento de centinelas, el tamaño máximo (P-06, provisional) y la validación física. **Parser v1 implementado en IoT-04.2** (§15); el tamaño máximo y la validación física siguen pendientes | IoT-04.1 |
| P-IOT-07 | Si el gateway verifica también la firma | Firmware |
| P-IOT-08 | ACK LoRa y su autenticación | Firmware |
| P-IOT-09 | ~~Campo `uptime`~~ **Resuelto en IoT-04.1:** excluido de la trama v1 | — |
| P-IOT-10 | Búfer del gateway: medio, capacidad, política al llenarse y ritmo de vaciado | Firmware |
| P-IOT-11 | Tolerancia entre `lora_rx_at` y `mqtt_rx_at` | IoT-04 |
| P-IOT-12 | ~~Longitud de la etiqueta HMAC~~ **Resuelto en IoT-03.2-R3:** HMAC-SHA256 completo de 32 bytes (`LORA_TAG_BYTES`), sin truncamiento; la longitud la fija el backend, nunca la trama | — |
| P-IOT-13 | Procedimiento ante borrado de NVS o reemplazo de placa | IoT-03 |
| P-IOT-14 | Tiempo casi real en React (consulta periódica o WebSocket) | IoT-07 |
| P-IOT-15 | Formato del mensaje `status` y *Last Will* | IoT-05 |
| P-IOT-16 | ~~Agrupación de tramas al vaciar el búfer~~ **Resuelto para v1 en IoT-04.1:** una trama por mensaje MQTT | — |
| P-IOT-17 | Límites de cola y persistencia de Mosquitto | IoT-06 |
| P-IOT-18 | Versión MQTT 3.1.1 o 5 | IoT-05 |
| P-IOT-19 | Cuarentena de mensajes rechazados | IoT-05 |
| P-IOT-20 | Algoritmo y formato del cifrado en reposo de las claves HMAC | IoT-03 |
| P-IOT-21 | Procedimiento de carga de claves en el coordinador | IoT-03 |
| P-IOT-22 | Periodo de transición en rotaciones de clave. **Gateways: resuelto en IoT-03.1** (sin transición). Coordinadores: IoT-03.2 | IoT-03.2 |
| P-IOT-23 | Cifrado de flash y arranque seguro del ESP32 | Firmware |
| P-IOT-24 | Autenticación de Mosquitto y certificados TLS en el ESP32 | IoT-06 |
| P-IOT-25 | ~~Coordinador ligado a uno o varios gateways~~ **Resuelto (aprobación IoT-02):** varios gateways autorizados de su misma finca | — |
| P-IOT-26 | Marca de agua por coordinador si existe retención | IoT-04 |
| P-IOT-27 | ~~Forma de las tablas de deduplicación (opción A o B)~~ **Resuelto en IoT-04.3-A (§16):** opción B, `ReceivedFrame` con `UNIQUE (coordinator, credential, boot_counter, seq)` y `Measurement` como hijas. Identidad aprobada en IoT-04.1 (D-01). Sin implementar | IoT-04.3-B |
| P-IOT-28 | Rangos y precisión por métrica. Métricas iniciales y unidades **resueltas en IoT-02.1**: `temperature` (°C) y `relative_humidity` (%) | IoT-04 |
| P-IOT-29 | Retención y agregación de históricos | IoT-08 |

## 7. Riesgos

| ID | Riesgo | Mitigación prevista |
|---|---|---|
| R-IOT-01 | Lecturas LoRa no cifradas; cualquiera puede escucharlas | Aceptado en v1; cifrado autenticado en una versión posterior si se requiere |
| R-IOT-02 | Interferencia o bloqueo de radio; pérdida de tramas | Monitorear huecos de `seq`; ACK LoRa opcional |
| R-IOT-03 | Robo de coordinador y extracción de su clave | Revocación, rotación, P-IOT-23 |
| R-IOT-04 | Metadatos del gateway no firmados por el coordinador | TLS + ACL; tratarlos como dato de menor confianza |
| R-IOT-05 | Hora aproximada en tramas con búfer; pérdida si el búfer es RAM | `clock_synced`, `buffered`, P-IOT-09, P-IOT-10 |
| R-IOT-06 | Proceso nuevo fuera de gunicorn; hoy no hay infraestructura de servicios en segundo plano | Comando de gestión y servicio dedicado en IoT-05/06 |
| R-IOT-07 | Cola bloqueada por errores mal clasificados | Clasificación explícita y máximo de reintentos |
| R-IOT-08 | Crecimiento de la tabla de mediciones | Índices por finca y sensor; P-IOT-29 |
| R-IOT-09 | Pérdida de la clave maestra de cifrado | Procedimiento de respaldo operativo |
| R-IOT-10 | Error en la ACL del broker | Barreras del backend (T1, T2) y pruebas de ACL |
| R-IOT-11 | `config/database.py` puede elegir Postgres; IoT solo está aprobado para MariaDB | P-IOT-02 |
| R-IOT-12 | Uso de la banda de 433 MHz sin parámetros validados normativamente | P-IOT-05 antes de despliegue en campo |
| R-IOT-13 | `pyproject.toml` no mide cobertura de `apps.iot` | Ajustar en IoT-02 |

## 8. Documentos

- [Arquitectura general](../modules/iot-arquitectura.md)
- [Flujo de una medición](../modules/iot-flujo-medicion.md)
- [Contrato LoRa](../modules/iot-contrato-lora.md)
- [Contrato MQTT](../modules/iot-contrato-mqtt.md)
- [Seguridad y autenticación](../modules/iot-seguridad.md)
- [Persistencia, confirmaciones y deduplicación](../modules/iot-persistencia-entrega.md)
- [Credenciales de gateway](../modules/iot-credenciales-gateway.md)
- [Credenciales de coordinador](../modules/iot-credenciales-coordinador.md)

## 9. Brechas de implementación (estado histórico al 2026-11-13)

Esta sección conserva el registro de brechas de esa fecha. Para el estado
vigente de la arquitectura y la implementación, véase la decisión BUILD
B0-ARCH-01 al inicio de este documento.

No existen consumidor MQTT, configuración de Mosquitto, API REST, recepción ni
persistencia de mediciones, ni firmware. IoT-02.1, IoT-02.2, IoT-03.1, IoT-03.2
e IoT-04.2 aportan los componentes de §10, §11, §12, §13 y §15; IoT-04.2 solo
interpreta la trama, no la recibe ni la guarda.

## 10. Implementación IoT-02.1

| Componente | Archivo | Qué hace |
|---|---|---|
| Catálogo de métricas | `apps/iot/catalog.py` | Define `temperature` (°C, temperatura) y `relative_humidity` (%, proporción); `is_known_metric`, `get_metric`, `metric_unit`; rechaza códigos desconocidos con `UnknownMetricError` |
| Identificadores públicos | `apps/iot/identifiers.py` | `generate_identifier`, `is_valid_identifier` (estricta), `normalize_identifier` (espacios y mayúsculas); `IdentifierKind` GW/CO/SN |
| Exigencia de MariaDB | `apps/iot/db_guard.py` | `require_mariadb()` lanza `ImproperlyConfigured` si `vendor != "mysql"` o `mysql_is_mariadb` no es `True` |

Especificación:

- **Identificadores:** prefijo `GW-`, `CO-` o `SN-` + 10 caracteres del alfabeto
  `ABCDEFGHJKMNPQRSTUVWXYZ23456789` (31 símbolos, sin `0 O 1 I L`; mismo
  conjunto que `CODE_ALPHABET` de las invitaciones, declarado como constante
  propia para que el formato persistido no dependa de otro módulo). Longitud
  total 13. Generación con `secrets.choice`. La unicidad definitiva será una
  restricción `UNIQUE` en MariaDB (IoT-02.2); ante colisión la persistencia
  reintentará hasta `MAX_GENERATION_ATTEMPTS = 5` veces. Un identificador
  nunca se reasigna.
- **Unidad:** se deriva siempre de la métrica; no se almacenará por separado.
- **MariaDB:** la guarda no se ejecuta al importar ni en `IotConfig.ready()`;
  el backend sigue arrancando sobre otros motores. Solo bloquea operaciones IoT
  cuando la invoquen los servicios de escritura y el consumidor. A diferencia de
  `auth_service` (que omite un bloqueo fuera de MariaDB), IoT falla de forma
  cerrada.

Explicación sencilla:

- **Catálogo:** una lista fija, escrita en el código, de lo que un sensor puede
  medir y en qué unidad. Si alguien pide una métrica que no está en la lista, el
  sistema la rechaza en lugar de inventarla.
- **Identificador público:** el "nombre de matrícula" de cada aparato, por
  ejemplo `GW-7K3M9QXT2A`. Se muestra al usuario, se graba en el dispositivo y
  viaja en los mensajes, sin revelar el número interno de la base de datos ni
  cuántos aparatos existen.
- **`secrets`:** el módulo de Python para valores que deben ser imposibles de
  adivinar. `random` sirve para simulaciones, pero sus resultados pueden
  predecirse.
- **Exigir MariaDB:** antes de guardar algo de IoT, el sistema comprueba que la
  base sea MariaDB. Si no lo es, se detiene con un error claro en lugar de
  guardar datos con reglas de integridad incompletas. El resto del backend no
  se ve afectado.

Pruebas: `apps/iot/tests/test_catalog.py`, `test_identifiers.py`,
`test_db_guard.py` (unitarias, sin base de datos).

## 11. Implementación IoT-02.2

### Modelos (`apps/iot/models.py`)

| Modelo | Tabla | Campos principales |
|---|---|---|
| `Gateway` | `iot_gateways` | `uid` (GW-), `farm` (PROTECT), `name`, `location_label`, estado y baja |
| `Coordinator` | `iot_coordinators` | `uid` (CO-), `farm` (PROTECT), `name`, `location_label`, estado y baja |
| `Sensor` | `iot_sensors` | `uid` (SN-), `farm`, `coordinator` (PROTECT), `channel`, `metric`, `active_channel`, estado y baja |
| `CoordinatorGateway` | `iot_coordinator_gateways` | `farm`, `coordinator`, `gateway` (PROTECT), `authorized_at/by`, `revoked_at/by`, `active_pair` |

Campos de estado comunes: `is_active`, `created_at`, `updated_at`,
`created_by`, `deactivated_at`, `deactivated_by` (usuarios con SET_NULL). PK
`BigAutoField`. La unidad del sensor se deriva del catálogo. No hay modelo de
grupos ni dependencia de avicultura. El coordinador no tiene conectividad IP.

### Integridad en la base de datos

- `UNIQUE (uid)` en las tres entidades y `CHECK` del formato con
  `REGEXP` (prefijo, longitud y alfabeto).
- `CHECK (metric IN (...))` con los códigos de `MetricCode`.
- Coherencia de estado: activo sin datos de baja; inactivo con fecha de baja.
- Un sensor activo por canal: `UNIQUE (coordinator, active_channel)`, con
  `active_channel` = `channel` si está activo y NULL si no.
- Una autorización vigente por pareja: `UNIQUE (coordinator, gateway,
  active_pair)`, con `active_pair` = 1 vigente y NULL revocada; `revoked_at >=
  authorized_at`.
- Los `CHECK` de la rama "activo" exigen `IS NOT NULL` explícito: en SQL un
  `CHECK` con resultado NULL se considera cumplido (las pruebas lo detectaron).
- **FK compuestas** (`iot.0002_composite_farm_fks`, `RunPython` con
  `atomic=False` porque MariaDB no admite DDL transaccional; idempotente desde
  IoT-02.2-R2):
  `iot_sensors (coordinator_id, farm_id)` y `iot_coordinator_gateways
  (coordinator_id, farm_id)` → `iot_coordinators (id, farm_id)`;
  `iot_coordinator_gateways (gateway_id, farm_id)` → `iot_gateways (id,
  farm_id)`, con `ON UPDATE/DELETE RESTRICT`. Se apoyan en `UNIQUE (id, farm)` e
  índices `(x, farm)` declarados en `0001`. Se ejecutan en MariaDB y PostgreSQL;
  en SQLite se omiten. Son reversibles.
- **Idempotencia y recuperación de `iot.0002`:** cada FK se busca en
  `information_schema` por esquema actual, tabla y nombre. Ausente: se crea (o
  se omite al revertir). Presente con la definición esperada: se omite (o se
  elimina al revertir). Presente con otra definición: `RuntimeError` sin tocar
  nada. Si una ejecución anterior se interrumpió y dejó solo parte de las FK, la
  migración no quedó registrada: basta con volver a ejecutar `migrate`. Una
  migración no registrada no se revierte con `migrate iot 0001`; primero se
  completa y después se revierte.
- `apps/iot/tests/test_model_support.py` (sin base de datos) vigila que sigan
  declarados `UNIQUE (id, farm)` y los índices `(x, farm)` que usan las FK.
- `iot.0001` depende solo de `accounts.0001` y `farms.0001` (como
  `inventario.0001`), para que revertir migraciones posteriores de esas apps no
  arrastre a IoT.

### Servicios (`apps/iot/services/`)

- `access.py`: lectura con `require_active_membership`. Escritura, en este
  orden (IoT-02.2-R2): `lock_active_membership` y rol en `IOT_MANAGE_ROLES`
  (solo administrador) con cuenta activa → `require_mariadb()` → validación de
  datos → operación y auditoría. Un no miembro recibe 404 y un integrante sin
  rol 403 aunque envíe datos inválidos o el motor no sea MariaDB.
- `devices.py`: alta, consulta, desactivación y reactivación de gateways y
  coordinadores. `sensors.py`: lo mismo para sensores. `authorizations.py`:
  autorizar, revocar e historial coordinador–gateway.
- Orden de bloqueo: Farm → FarmMembership → Coordinator → Gateway → filas
  dependientes (por PK). El bloqueo de `Farm` serializa las escrituras IoT de
  una finca.
- Desactivar un gateway revoca sus autorizaciones vigentes. Desactivar un
  coordinador revoca sus autorizaciones y desactiva sus sensores.
- Reactivar gateways, coordinadores y sensores exige motivo, queda auditado y no
  restaura autorizaciones ni sensores. Un sensor solo se reactiva con su
  coordinador activo, métrica del catálogo y canal libre.
- Canal: sin máximo funcional. Se valida contra el rango del tipo de columna en
  el motor activo (`connection.ops.integer_field_range`): en MariaDB
  `smallint UNSIGNED`, 0 a 65535. No es un límite de hardware.
- Recursos de otra finca y `uid` mal formados responden 404.
- Colisión de `uid`: hasta `MAX_GENERATION_ATTEMPTS` intentos con punto de
  guardado; después, 500.

### Permisos y auditoría

- `apps/farms/capabilities.py`: `IOT_MANAGE_ROLES = {administrador}`,
  `can_read_iot` (membresía y finca activas) y `can_manage_iot` (además, rol
  administrador y cuenta activa). Son diez claves en total. No hay entradas
  `iot.*` en el catálogo general de permisos.
- `trazabilidad.0009`: 11 eventos `iot_*` (alta, desactivación y reactivación de
  cada dispositivo; autorización y revocación). Se registran en la misma
  transacción; los payloads solo llevan `uid`, nombre, etiqueta, canal, métrica
  y estado, nunca claves ni credenciales. Las cascadas indican `cause`.
- `config/settings_test_disposable_isolated.py` incluye `trazabilidad.0009` en
  su grafo autorizado.

### Pruebas

`apps/iot/tests/test_models.py`, `test_services.py`, `test_permissions.py`,
`test_concurrency.py` y `test_migrations.py`, ejecutadas solo con el
orquestador desechable (MariaDB 11.8.8).

### Pendientes

- Límite funcional de canales (cableado y firmware). Hoy solo rige el rango de
  la columna.
- **Discrepancia del canal 0 (detectada en IoT-04.2):** el parser v1 rechaza el
  canal 0 (IoT-04.2-4), pero `services/sensors._validate_channel` admite el
  rango completo de la columna (`PositiveSmallIntegerField`, 0–65535 en
  MariaDB), así que se puede registrar un sensor en el canal 0 que nunca
  recibirá lecturas válidas. Resolución pendiente de decisión; IoT-04.2 no
  modifica el modelo ni el servicio.
- **Cobertura de `apps.iot`: no medida.** El orquestador ejecuta pytest con
  `-o addopts=` y un entorno hijo de lista blanca, sin opción de cobertura; las
  pruebas con base de datos no pueden ejecutarse fuera de él. Medirla requiere
  una tarea autorizada independiente (por ejemplo, un modo `-Coverage` del
  orquestador que escriba los datos en `OutputDirectory`). Una cifra calculada
  solo con las pruebas estáticas no es representativa.
- `entity_id` de auditoría: los dispositivos IoT usan su `uid` público y el
  resto de la trazabilidad, la clave interna (las autorizaciones IoT siguen esta
  última). Unificar el criterio requiere decisión.
- Las 16 pruebas Redis de `apps/farms/tests` (`-Validate -WithRedis`) no se
  ejecutaron en IoT-02.2; IoT no modifica ese código.

## 12. Implementación IoT-03.1: credenciales de gateway

Detalle completo en
[iot-credenciales-gateway.md](../modules/iot-credenciales-gateway.md).

### Decisiones aprobadas (IoT-03.0)

1. Autenticación MQTT futura con credenciales administradas por Mosquitto.
2. Rotación sin transición: revocación inmediata de la anterior.
3. Desactivar un gateway revoca todas sus credenciales activas.
4. Desactivar la finca bloquea la autenticación sin revocar; si se reactiva,
   una credencial no revocada vuelve a ser válida.
5. `GATEWAY_CREDENTIAL_HMAC_KEY` independiente de `SECRET_KEY`; obligatoria y
   validada al arrancar en producción (`DEBUG=False`); en desarrollo y pruebas
   falla de forma cerrada al usarse.
6. Secreto `gws_` + `secrets.token_urlsafe(32)`.
7. `last_used_at` aplazado a IoT-05.
8. Sin caducidad automática.
9. Claves HMAC de coordinadores independientes, en IoT-03.2.

### Implementación

- `config/gateway_credential_key.py` (validador), `config/platform.py` y
  `config/settings.py` (`resolve_gateway_credential_key`).
- `apps/iot/gateway_credentials.py`: huella
  `HMAC-SHA256(clave, "agrotech:gw-cred:v1:{gateway_uid}:{secreto}")`,
  `compare_digest`, `key_version` (hoy 1).
- `GatewayCredential` (`iot.0003`): huella única con `CHECK` hexadecimal,
  `active_slot` con `UNIQUE (gateway, active_slot)`, `CHECK` de estado con
  `IS NOT NULL` explícito, `revoked_at >= issued_at`, PROTECT, sin `farm`. La
  migración depende solo de `accounts.0001` e `iot.0002`.
- Servicios (`apps/iot/services/credentials.py`): emitir, rotar
  (`rotated`/`reissued`), revocar (`admin`/`compromised`), verificar e
  historial. Escritura: autorización → `require_mariadb()` → validación →
  operación y auditoría; bloqueo Farm → FarmMembership → Gateway → credenciales.
- `devices.deactivate_gateway` revoca la credencial vigente en la misma
  transacción; la reactivación no la restaura.
- `trazabilidad.0010`: 5 eventos `iot_gateway_credential_*`, también añadida al
  grafo de `config/settings_test_disposable_isolated.py`.
- Los datos auditados usan `gwcred_id`, `previous_gwcred_id` y
  `revoked_gwcreds`: `record_audit_event` rechaza toda clave cuyo nombre
  **contenga** un término sensible (por ejemplo, `credential`), y esa protección
  no se relaja.

### Despliegue

Producción no arranca sin `GATEWAY_CREDENTIAL_HMAC_KEY` válida. Debe
configurarse antes de desplegar esta versión y respaldarse: perderla invalida
todas las huellas.

### Pendientes

- **Integración con Mosquitto (P-IOT-24):** la huella HMAC de Django no equivale
  al hash de contraseña de Mosquitto. Falta resolver la sincronización de
  credenciales y la propagación de revocaciones sin persistir secretos en texto
  claro. No se ha inventado ningún mecanismo.
- Rotación operativa de la clave del servidor: diseño documentado
  (versiones de clave), sin implementar.
- `last_used_at` (IoT-05) y claves de coordinadores (IoT-03.2, P-IOT-20 a 22).
- Cobertura de `apps.iot` sin medir (ver §11).

## 13. Implementación IoT-03.2: credenciales de coordinador

Detalle completo en
[iot-credenciales-coordinador.md](../modules/iot-credenciales-coordinador.md).

### Decisiones aprobadas (IoT-03.2)

1. AES-256-GCM con clave de coordinador de 32 bytes, nonce aleatorio de 12 bytes
   en campo propio y etiqueta GCM dentro del ciphertext (48 bytes).
2. AAD `agrotech:co-cred:v1:{coordinator_uid}:{master_key_version}`.
3. `COORDINATOR_KEY_ENCRYPTION_KEY` = Base64 de exactamente 32 bytes,
   independiente de `SECRET_KEY`, versionada y validada al arrancar en
   producción; en desarrollo y pruebas falla de forma cerrada al usarse.
4. Rotación inmediata, sin coexistencia; la anterior se revoca en la misma
   transacción. Reemisión por pérdida con causa `reissued`; además reinicia la
   ventana antirreplay en esa misma transacción para permitir `boot_counter` 0.
   La rotación ordinaria no reinicia la ventana.
5. Al desactivar el coordinador se revocan sus credenciales vigentes; reactivar
   no las restaura.
6. Enum independiente `CoordinatorRevocationCause`: `rotated`, `reissued`,
   `compromised`, `coordinator_deactivated`, `admin`.
7. Ventana antirreplay de cuatro arranques recientes; la identidad única
   `(coordinator_uid, boot_counter, seq)`, la deduplicación y la transacción con
   la medición son de IoT-04. `accept_coordinator_sequence` revalida
   `coordinator_id`, `uid` y `farm_id` contra la fila persistida y no toca la
   ventana si no coinciden o si el coordinador/finca están inactivos.
   *(Nota IoT-04.1: identidad sustituida por D-01,
   `(coordinator_id, credential_id, boot_counter, seq)`, §14.)*
8. No se inventa el contexto HMAC (P-IOT-06). El tag (P-IOT-12) es el
   HMAC-SHA256 completo de 32 bytes, sin truncar (IoT-03.2-R3, §13 R3).
   *(Nota IoT-04.1: contexto resuelto por D-10, `agrotech:lora-frame:v1`, §14.)*
9. `master_key_version` (versión AES-GCM persistida) se limita a 1–65535, el
   rango de `SMALLINT UNSIGNED`; no se confunde con la `key_version` de la trama
   LoRa. Un `IntegrityError` solo se traduce a `409` cuando el conflicto real es
   la credencial vigente.
10. La verificación calcula un HMAC descartable cuando falta la credencial o el
    ciphertext no autentica, para no distinguir el motivo por el tiempo.
11. El parser de IoT-04 debe construir `signed_bytes` con `coordinator_uid`,
    `boot_counter`, `seq` y la `key_version` de la trama; una discrepancia entre
    lo firmado y los campos interpretados se rechaza. El servicio de credenciales
    no reconstruye esos bytes.
    *(Nota IoT-04.2: implementado; `validate_coordinator_frame` vuelve a
    interpretar `signed_bytes + tag` con el parser v1, §15.)*

### Implementación

- `config/coordinator_key_encryption_key.py`: decodificación Base64 de 32 bytes,
  versión vigente e histórica, anillo `MasterKeyRing` y validación de arranque.
- `apps/iot/coordinator_credentials.py`: generación, cifrado/descifrado
  AES-256-GCM, AAD, HMAC-SHA256 de trama con tag fijo de 32 bytes y
  validación de la trama interpretada (ampliada en IoT-04.2 con la comprobación
  de coherencia M-02, §15).
- `CoordinatorCredential` y `CoordinatorBootWindow` (`iot.0004`, depende de
  `iot.0003` y `accounts.0001`): ciphertext/nonce con `CHECK` de longitud,
  `UNIQUE (coordinator, active_slot)`, estado coherente con `IS NOT NULL`
  explícito, `revoked_at >= issued_at`, `PROTECT` y sin `farm`. La revocación
  exige causa y fecha; `issued_by`/`revoked_by` usan `SET_NULL` para conservar el
  historial si se borra la cuenta. `iot.0005` renombra `key_version` a
  `master_key_version` y ajusta su `CHECK` a 1–65535.
- Servicios (`apps/iot/services/coordinator_credentials.py`): emitir, rotar,
  revocar, historial, verificación de trama y ventana antirreplay
  (`accept_coordinator_sequence`, exige transacción del llamador). La reemisión
  reinicia la ventana con `_reset_boot_window`; la aceptación valida identidad y
  finca antes de escribir.
- `devices.deactivate_coordinator` revoca las credenciales en la misma
  transacción y añade `revoked_coordcreds` a la auditoría del coordinador.
- `trazabilidad.0011`: 5 eventos `iot_coordinator_credential_*`; el campo
  `event_type` pasa a `max_length=48` porque
  `iot_coordinator_credential_cascade_revoked` mide 42 caracteres. La migración y
  el grafo de `config/settings_test_disposable_isolated.py` se actualizan.
- Datos auditados: `coordcred_id`, `coordinator_uid`, `master_key_version`,
  `active`, `revocation_cause`, `previous_coordcred_id`,
  `boot_window_rows_reset` y `revoked_coordcreds`; nunca clave, ciphertext,
  nonce ni material HMAC.

### Despliegue

Producción no arranca sin `COORDINATOR_KEY_ENCRYPTION_KEY` válida. Para rotar la
clave maestra se publica la versión anterior en
`COORDINATOR_KEY_ENCRYPTION_KEY_PREVIOUS`, se sube
`COORDINATOR_KEY_ENCRYPTION_KEY_VERSION` y se conserva la anterior mientras
existan credenciales cifradas con ella.

### Pendientes

- **P-IOT-06:** parcial. Contexto y estructura binaria aprobados en IoT-04.1
  (§14); parser v1 implementado en IoT-04.2 (§15). Siguen pendientes el tamaño
  máximo y la validación física.
- **P-IOT-12:** resuelto en IoT-03.2-R3 (32 bytes, sin truncar).
- **M-02:** resuelto en IoT-04.2 (§15).
- **M-03:** pendiente; requisito obligatorio de la ingestión (ver «IoT-03.2-R3»).
- **P-IOT-21:** procedimiento físico de carga de la clave.
- **IoT-04:** sigue pendiente la ingestión: deduplicación de `seq`, persistencia
  de tramas y mediciones y transacción conjunta con la ventana.
- **IoT-05:** `last_used_at` u observabilidad equivalente.

### IoT-03.2-R3: correcciones de la auditoría R2

- **H-01 (corregido):** `verify_lora_hmac` y `validate_coordinator_frame` exigen
  un tag de exactamente `LORA_TAG_BYTES = 32` y comparan el HMAC completo con
  `hmac.compare_digest`; ya no recortan el HMAC esperado a la longitud recibida.
  Un tag de cualquier otra longitud se rechaza antes de consultar la base.
- **B-01:** prueba de conservación de datos de `iot.0005` (ida, vuelta y
  reaplicación con valores 7 y 65535).
- **B-03:** prueba del reinicio con la ventana completa (cuatro filas).
- **B-02:** las comprobaciones de migraciones se hacen sin conexión
  (`MigrationLoader(None)`), y el orquestador queda fijado a `127.0.0.1:3317`.
  En la máquina de desarrollo hay servidores reales en 3306 y 3307: no se usan.

**Requisitos obligatorios de IoT-04 (no implementados en R3):**

- **M-02, campos ligados a la firma.** `coordinator_uid`, `key_version`,
  `boot_counter` y `seq` deben **derivarse de los mismos `signed_bytes`** que se
  verifican, mediante un único parser. No se aceptan valores independientes que
  puedan contradecir lo firmado. IoT-04 debe incluir pruebas de manipulación de
  cada campo, y cada una debe terminar en rechazo.
- **M-03, verificación y aceptación atómicas.** La verificación
  (`verify_coordinator_frame`) y la aceptación (`accept_coordinator_sequence`)
  deben ejecutarse en una misma operación transaccional. Antes de aceptar se
  revalida bajo bloqueo que la credencial verificada sigue vigente; una trama
  cuya credencial se revocó entre ambos pasos se rechaza. IoT-04 debe incluir
  una prueba de esa carrera. `AuthenticatedCoordinatorFrame` es público: nada
  impide construirlo sin autenticar, así que solo debe crearlo la verificación.

*Estado posterior (IoT-04.2):* **M-02 resuelto** (§15). **M-03 sigue
pendiente.**

## 14. IoT-04.1: contrato binario LoRa v1 (en curso, no cerrado)

Detalle en [contrato LoRa §2.7](../modules/iot-contrato-lora.md),
[contrato MQTT §2.2](../modules/iot-contrato-mqtt.md) y
[persistencia §2.3](../modules/iot-persistencia-entrega.md). IoT-04.1 es solo
documentación. El parser v1 se implementó después, en IoT-04.2 (§15); no hay
modelos ni migraciones de tramas o mediciones.

### Aprobado

| ID | Decisión |
|---|---|
| D-01 | Identidad persistente de deduplicación `(coordinator_id, credential_id, boot_counter, seq)`, con `UNIQUE` en MariaDB; distinta de la ventana anti-replay |
| D-02 / P-01 | `key_version`: uint16 big-endian, 1–65535, generación de la credencial por coordinador (primera = 1, incrementa en cada rotación y reemisión, nunca se reutiliza ni se reinicia). No es `master_key_version`. Al agotarse: se detienen las rotaciones y se exige un procedimiento administrativo controlado; el uid no cambia automáticamente |
| D-03 | `coordinator_uid`: 13 bytes ASCII, sin terminador, validación canónica estricta |
| D-04 | Consumidor MQTT en IoT-06 |
| D-05 | Una trama LoRa por mensaje MQTT; `frame` en Base64 sin alterar |
| D-06 | Big-endian en todos los campos numéricos |
| D-07, D-08 / P-07 | `boot_counter` y `seq` uint32 big-endian. Sin desbordamiento silencioso: si `seq` se agota, se detiene la transmisión y se requiere un reinicio controlado; `boot_counter` se incrementa y se persiste durante el siguiente arranque físico. Si `boot_counter` se agota, se detiene la transmisión y se requiere reprovisionamiento controlado. Nunca se reinicia automáticamente a cero |
| D-09 / P-03, P-04 | Enteros escalados ×100: temperatura int16 (−32768 reservado para inválido); humedad uint16, rango 0–10000 (65535 reservado para inválido) |
| D-10 / P-02 | HMAC-SHA256 completo de 32 bytes al final, sobre `contexto || signed_bytes`; contexto ASCII `agrotech:lora-frame:v1` (22 bytes), no transmitido |
| P-08 | `frame_version` uint8, versión inicial 1; versiones desconocidas se rechazan; parser explícito por versión |
| P-09 | `uptime` y `grupo` excluidos de v1 |
| P-05 | Bloque de lectura de **5 bytes**: `channel` uint16 BE (offset relativo 0), `metric_code` uint8 (2; 1 = `temperature`, 2 = `relative_humidity`) y `value` int16/uint16 BE según la métrica (3). El `metric_code` debe coincidir con la métrica del sensor registrado en el canal; métrica incompatible o canal duplicado se rechazan. El bloque de 4 bytes sin `metric_code` es un antecedente descartado |

Cabecera aprobada (25 bytes): `frame_version` (0, 1 B), `coordinator_uid` (1,
13 B), `key_version` (14, 2 B), `boot_counter` (16, 4 B), `seq` (20, 4 B) y
`reading_count` (24, 1 B; máximo provisional). Después, `reading_count` bloques
de 5 bytes desde el offset 25 y la etiqueta de 32 bytes en el offset
`25 + 5 × reading_count`.

### Provisional

- Máximo de 16 lecturas por trama.
- Tamaño: `total_bytes = 25 + (5 × reading_count) + 32`: 67 bytes con 2
  lecturas, 77 con 4 y 137 con 16. 137 bytes **no** es una capacidad física
  validada y 255 bytes **no** es un tamaño operativo garantizado.

### Pendiente de decisión

- Rango físico válido de temperatura: depende del modelo real del sensor y de
  su hoja técnica.
- Tamaño máximo del mensaje MQTT y variante exacta de Base64.
- Tratamiento de una lectura con el valor reservado de inválido (centinela).

### Pendiente de validación física

- Límite real de carga útil del SX1278.
- Configuración real de los registros y del controlador.
- Tiempo en aire y parámetros de radio (SF, BW, CR, preámbulo y CRC).
- Normativa colombiana aplicable a 433 MHz (P-IOT-05).

### Implementación posterior (no autorizada)

El campo de generación, `CoordinatorKeyGeneration` y `credential_id` en
`AuthenticatedCoordinatorFrame` se implementaron en IoT-04.3-C1 (§17). Siguen
pendientes las tablas de tramas y mediciones y el requisito M-03 (§13). El parser
v1 y M-02 se implementaron en IoT-04.2 (§15).

## 15. IoT-04.2: parser binario LoRa v1

Detalle técnico en [contrato LoRa §2.8](../modules/iot-contrato-lora.md).

### Alcance implementado

Interpretación pura de la trama v1 (§14) y corrección M-02. Sin Base64,
ingestión, persistencia, deduplicación, MQTT, API REST, modelos ni migraciones.

### Decisiones aprobadas (IoT-04.2)

Los identificadores son propios de esta fase y no se confunden con D-01 a D-10
de §14.

| ID | Decisión |
|---|---|
| IoT-04.2-1 | Salida en una estructura nueva e inmutable `LoraFrameV1` con una tupla inmutable de `LoraReading`; `signed_bytes`, `tag` y `coordinator_uid` excluidos de `repr`. El adaptador `to_coordinator_frame()` reutiliza `ParsedCoordinatorFrame` sin romper su interfaz |
| IoT-04.2-2 | El parser rechaza un `metric_code` desconocido (solo 1 = `temperature` y 2 = `relative_humidity`) y los canales duplicados, aunque tengan métricas distintas |
| IoT-04.2-3 | El parser no aplica rangos físicos ni decide qué hacer con un centinela; solo lo identifica (−32768 en temperatura, 65535 en humedad). La política queda pendiente |
| IoT-04.2-4 | Canales válidos 1–65535; el canal 0 es un rechazo estructural. Sin cambios en el modelo `Sensor` ni migraciones |
| IoT-04.2-5 | `validate_coordinator_frame` vuelve a interpretar `signed_bytes + tag` y exige que `coordinator_uid`, `key_version`, `boot_counter`, `seq`, `signed_bytes` y `tag` coincidan con los campos declarados (M-02) |

Se aplican además las decisiones de contrato de §14: D-02 (rango de
`key_version`), D-03 (uid), D-06 (big-endian), D-07/D-08 (contadores), D-09
(escalas y centinelas), D-10 (HMAC y contexto), P-05 (bloque de lectura), P-08
(versión) y el máximo provisional de 16 lecturas.

### Archivos

- Creados: `apps/iot/lora_frame.py` (parser), `apps/iot/tests/lora_vectors.py`
  (vectores construidos con `struct.pack`, independientes del parser) y
  `apps/iot/tests/test_lora_frame.py`.
- Modificados: `apps/iot/coordinator_credentials.py` (IoT-04.2-5, importación
  del parser e invariante del tamaño del tag; sin cambios en `__all__` ni en la
  API) y los helpers de cuatro pruebas de IoT-03.2
  (`test_coordinator_credentials_crypto.py`, `test_coordinator_credentials_r2.py`,
  `test_coordinator_credential_concurrency.py`,
  `test_coordinator_credential_services.py`), que pasan a usar tramas v1 reales.

### Corrección M-02

Los campos declarados de un `ParsedCoordinatorFrame` deben salir exactamente de
sus `signed_bytes`; un objeto contradictorio se rechaza antes de consultar la
base. Coherencia no es autenticidad: la da la verificación HMAC. Las lecturas no
forman parte de `ParsedCoordinatorFrame` y solo las protege el HMAC.

### Validación (informe de IoT-04.2-BUILD)

- `apps/iot/tests` completo mediante el orquestador desechable
  (`-Validate`, MariaDB 11.8.8 en `127.0.0.1:3317`): 753 pruebas aprobadas,
  0 fallos. La ronda dirigida previa dio 394 aprobadas.
- Ruff sin errores en los archivos del alcance.
- Sin Redis (`-WithRedis` no usado), sin bases reales (3306/3307) y sin
  operaciones Git.

### Pendientes

- Validación física del SX1278 (carga útil, registros, tiempo en aire, CRC) y
  normativa de 433 MHz en Colombia (P-IOT-05).
- Cierre del máximo de 16 lecturas; rango físico de temperatura; rangos y
  precisión por métrica (P-IOT-28); política de centinelas.
- Tamaño máximo del mensaje MQTT, decodificación Base64 y consumidor MQTT
  (IoT-06).
- M-03.
- Modelos de tramas recibidas y mediciones, `UNIQUE` de deduplicación
  `(coordinator_id, credential_id, boot_counter, seq)` y persistencia
  transaccional (P-IOT-27).
- Validaciones de ingestión: gateway, coordinador, finca, sensor registrado y
  correspondencia de métrica.
- Discrepancia del canal 0 con el servicio de sensores (§11).
- NVS (P-IOT-13), carga de la clave (P-IOT-21), firma en el gateway (P-IOT-07) y
  ACK (P-IOT-08).

## 16. IoT-04.3-A: diseño de persistencia e ingestión (aprobado, sin implementar)

Detalle en [persistencia §2.6](../modules/iot-persistencia-entrega.md). Solo
diseño: no hay modelos, migraciones ni servicio de ingestión. La implementación
(IoT-04.3-B en adelante) requiere autorización propia.

### Hallazgos que motivan las decisiones (verificados en el código)

- Todas las operaciones administrativas IoT bloquean primero la fila de `Farm`
  (`lock_manage_membership` → `farms._lock_membership`: Farm → FarmMembership) y
  después sus dispositivos. `deactivate_farm` bloquea solo Farm y FarmMembership
  y no desactiva dispositivos.
- `authorizations.lock_active_authorizations` usa `select_for_update()` con
  `select_related("coordinator", "gateway")`: en MariaDB el `FOR UPDATE` con JOIN
  bloquea también las filas unidas. `deactivate_gateway` bloquea así
  Gateway → (CoordinatorGateway + Coordinator); `authorize_gateway` y
  `revoke_gateway_authorization` bloquean Coordinator → Gateway. El ciclo latente
  no se produce hoy porque ambas rutas bloquean antes la misma `Farm`.
- `verify_coordinator_frame` lee la credencial sin bloqueo (M-03 abierto).
- `config/database.py` no fija el nivel de aislamiento; el nivel efectivo no se
  ha verificado.

### Decisiones aprobadas (IoT-04.3-A)

Identificadores propios de esta fase; no se confunden con D-01 a D-10 (§14) ni
con IoT-04.2-1 a IoT-04.2-5 (§15).

| ID | Decisión | Justificación técnica |
|---|---|---|
| IoT-04.3-1 | `ReceivedFrame.raw_frame` guarda la trama binaria completa recibida | Permite reverificar, auditar y distinguir un reenvío idéntico de un conflicto de identidad. No contiene secretos (el tag no lo es). `signed_bytes` y `tag` no se guardan aparte: son cortes de `raw_frame` |
| IoT-04.3-2 | Persistir únicamente tramas autenticadas y aceptadas en `ReceivedFrame`, sin añadir una columna de estado para tramas inválidas o pendientes | Una trama no autenticada nunca ocupa una identidad `(coordinator, credential, boot_counter, seq)`, así que un atacante no puede bloquear tramas legítimas por adelantado. La existencia de la fila significa "aceptada y confirmada" |
| IoT-04.3-3 | La ingestión bloquea `Farm` con `SELECT … FOR UPDATE` antes del coordinador y la conserva hasta `COMMIT` o `ROLLBACK`. Orden: Farm → Coordinator → Gateway → CoordinatorGateway → Credential → BootWindow → Sensors. No usa `lock_active_authorizations` (su JOIN añade bloqueos fuera de ese orden) | Mismo primer bloqueo que todas las operaciones administrativas: elimina el ciclo con `deactivate_gateway` y serializa la ingestión frente a la desactivación de la finca. Una lectura sin bloqueo permitiría confirmar una trama después de la baja de la finca y reabriría el ciclo. Punto de linealización: la obtención del bloqueo de `Farm` |
| IoT-04.3-4 | FK compuesta `Measurement (frame_id, coordinator_id, farm_id, measured_at)` → `ReceivedFrame (id, coordinator_id, farm_id, measured_at)` | La base garantiza que los campos desnormalizados de la medición coinciden con su trama. Su compatibilidad con MariaDB 11.8 sobre DATETIME(6) **no está validada**: se comprobará en IoT-04.3-B con pruebas aisladas |
| IoT-04.3-5 | `ReceivedFrame` referencia la fila concreta de `CoordinatorGateway` con FK compuesta `(authorization_id, coordinator_id, gateway_id, farm_id)` → `(id, coordinator_id, gateway_id, farm_id)` | Las FK compuestas existentes de `CoordinatorGateway` ya garantizan que coordinador, gateway y autorización son de la misma finca; esta FK lo extiende a la trama y deja constancia de la autorización usada |
| IoT-04.3-6 | La autorización registrada es la vigente al procesar y aceptar la trama, no la vigente cuando el gateway la recibió. Sin autorización vigente, la trama se rechaza. No se infiere la autorización histórica a partir de `gateway_received_at` | `gateway_received_at` es un dato del gateway, no firmado por el coordinador, y su reloj puede no estar sincronizado. La única referencia verificable es el punto de linealización (IoT-04.3-3) |
| IoT-04.3-7 | Índices `Measurement (sensor, measured_at)` y `(farm, measured_at)`, más los obligatorios de las claves únicas y las FK compuestas | `(sensor, measured_at)` cubre la historia y la última lectura de un sensor; el sensor ya determina la finca (FK compuestas), así que la autorización por finca se aplica resolviendo antes el sensor. `(farm, measured_at)` cubre la línea temporal de la finca. Sustituye al índice previsto `(farm, sensor, measured_at)` |
| IoT-04.3-8 | Política selectiva de sensores: sensor registrado, activo y con métrica coincidente → se guarda la medición; sensor registrado pero desactivado → se acepta la trama, se conserva `raw_frame` y se omite solo esa medición; canal sin sensor registrado → se rechaza la trama entera; métrica incompatible → se rechaza la trama entera | Desactivar un sensor (por ejemplo, averiado) no debe hacer perder las demás lecturas válidas del coordinador. Un canal desconocido o una métrica distinta indican un error de configuración o de seguridad. No hay estado adicional de medición: una lectura omitida no se guarda como medición |
| IoT-04.3-9 | Los conflictos de identidad (misma identidad, bytes distintos) se registran en una tabla independiente, propuesta `iot_frame_conflicts`, con la trama aceptada original, los bytes conflictivos, su SHA-256, el gateway, `server_received_at` y un `UNIQUE` que evita registrar dos veces el mismo conflicto | No sobrescribe la trama original ni se confunde con un error transitorio. `AuditEvent` no es adecuado: no está pensado para bytes y `record_audit_event` rechaza cualquier clave que contenga `credential`. Nunca se registran secretos, claves HMAC ni credenciales descifradas |
| IoT-04.3-10 | Una trama autenticada y autorizada puede aceptarse aunque todos sus sensores estén desactivados: se conserva `ReceivedFrame` y se crean cero filas `Measurement`. No es un error de persistencia (aprobada en IoT-04.3-B) | Consecuencia directa de IoT-04.3-8: la trama es válida y queda registrada con su `raw_frame`; omitir las lecturas de sensores desactivados no la invalida. El esquema no obliga a que una trama tenga mediciones |

Relacionado: P-IOT-27 queda resuelto (opción B, §6). La identidad de
deduplicación sigue siendo D-01. M-03 se resolverá con el orden de IoT-04.3-3
(credencial bloqueada durante la verificación HMAC); sigue pendiente hasta
implementarlo.

### Pendientes (no resueltos en IoT-04.3-A)

- P-IOT-11: tolerancia del reloj del gateway.
- P-IOT-19: cuarentena de tramas inválidas.
- P-IOT-29: política de retención; particionado futuro (MariaDB no admite FK en
  tablas particionadas).
- Política de centinelas y rangos físicos (P-IOT-28); máximo definitivo de
  lecturas por trama.
- Tamaño máximo del mensaje MQTT, Base64 y consumidor MQTT (IoT-06).
- Compatibilidad de la FK compuesta sobre DATETIME(6) en MariaDB 11.8
  (IoT-04.3-4), por validar en IoT-04.3-B.
- Nivel de aislamiento efectivo de la conexión.
- ~~Pregunta abierta: trama autenticada y autorizada con todos sus sensores
  desactivados~~ **Resuelta en IoT-04.3-B** (IoT-04.3-10): se acepta con cero
  `Measurement`.

## 17. IoT-04.3-C1: generaciones persistentes de credencial

Implementa D-02 (§14) sin modificar las decisiones ya aprobadas.

### Decisiones de implementación

| ID | Decisión | Justificación |
|---|---|---|
| IoT-04.3-C1-1 | `CoordinatorCredential.key_version` (uint16, 1-65535) identifica la generación de la clave HMAC; `master_key_version` sigue identificando la clave maestra AES-GCM | Son conceptos independientes: rotar la clave del coordinador no rota la maestra ni al revés |
| IoT-04.3-C1-2 | `CoordinatorKeyGeneration` guarda por coordinador `last_key_version` y `updated_at`, con `UNIQUE` implícito por PK y `CHECK` 1-65535 | El contador durable impide reutilizar generaciones tras revocar o desactivar; no se deriva del historial ni de `master_key_version` |
| IoT-04.3-C1-3 | La primera emisión crea el contador en 1; rotación y reemisión usan `last + 1`; la revocación no lo reinicia | Conserva la identidad de la clave durante todo el ciclo de vida del coordinador |
| IoT-04.3-C1-4 | `iot.0009` añade `key_version` nullable y la tabla del contador, sin `RunPython` ni backfill; es reversible | No inventa generaciones para credenciales históricas y permite desplegar antes de regularizar el legado |
| IoT-04.3-C1-5 | Un coordinador con credenciales y sin contador falla cerrado al emitir o rotar (`LegacyGenerationUnresolved`); una credencial vigente con `key_version` NULL no autentica | Evita adoptar silenciosamente generaciones ambiguas; el legado requiere un procedimiento administrativo explícito |
| IoT-04.3-C1-6 | `verify_coordinator_frame` compara `key_version` con la generación persistida **después** del HMAC y devuelve `credential_id` y la generación validada | Una trama no autenticada no debe revelar la generación vigente; la ingestión necesita la credencial concreta (D-01) |
| IoT-04.3-C1-7 | Al llegar a 65535 se detienen emisión y rotación con `CoordinatorGenerationExhausted`; la credencial vigente no se revoca, el contador no desborda y se audita `iot_coordinator_credential_generation_exhausted` | El rango uint16 no se reinicia ni se reutiliza; el servicio humano decide la regularización |
| IoT-04.3-C1-8 | El orden de bloqueo se amplía a Farm → Coordinator → Gateway → CoordinatorGateway → Credential → CoordinatorKeyGeneration → BootWindow → Sensors | Mantiene el primer bloqueo común y evita ciclos con la desactivación; el contador solo se bloquea en emisión/rotación |

### Archivos

- `apps/iot/models.py`: campo y restricciones de `key_version`, modelo
  `CoordinatorKeyGeneration`.
- `apps/iot/services/coordinator_credentials.py`: asignación, coherencia,
  legado, agotamiento, comparación tras el HMAC, `credential_id` y auditoría.
- `apps/iot/migrations/0009_coordinator_key_generation.py`.
- `apps/trazabilidad/models.py` y `trazabilidad.0012`: evento de agotamiento.
- `apps/iot/tests/test_coordinator_generation.py` y adaptaciones de las pruebas
  de servicios, concurrencia, replay y migraciones.
- `config/settings_test_disposable_isolated.py` y
  `tests/test_settings_test_disposable_isolated.py`: grafo aislado y catálogo
  actualizados para `trazabilidad.0012`.

### Verificación

Suite ejecutada con `tools/run_permissions_disposable.ps1 -Validate` sobre
MariaDB 11.8 desechable: **844 pruebas de IoT y trazabilidad aprobadas**.
Incluye primera emisión, rotaciones, revocación, reemisión, legado NULL,
fail-closed, generación correcta/anterior/futura, agotamiento, restricciones,
atomicidad, concurrencia y migración ida/vuelta. Además, **45 pruebas estáticas
de la configuración aislada aprobadas** con `config.settings_test`. M-03 y la
ingestión siguen pendientes.

