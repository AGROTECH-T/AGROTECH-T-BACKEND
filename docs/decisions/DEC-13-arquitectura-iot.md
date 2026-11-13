# DEC-13 — Arquitectura y contratos técnicos del módulo IoT

- **Identificador:** DEC-13
- **Título:** Arquitectura y contratos técnicos del módulo IoT
- **Estado de decisión:** Aprobada (parcial: los puntos de §6 siguen pendientes)
- **Estado de implementación:** Parcial. IoT-02.1: catálogo, identificadores y
  exigencia de MariaDB (§10). IoT-02.2: modelos, FK compuestas, servicios,
  capacidades y auditoría (§11). Sin MQTT, API REST ni telemetría.
- **Alcance:** BUILD IoT-01, IoT-02.1, IoT-02.2
- **Fecha:** 2026-11-12
- **Relación:** DEC-01 (desactivación lógica de fincas), DEC-02 (roles por
  membresía), DEC-11 (capacidades por finca)

## 1. Propósito

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
| T3 | Identidad de trama (`coordinator_uid`, `boot_counter`, `seq`); `boot_counter` persistente en NVS, `seq` en RAM | [contrato LoRa §2.4](../modules/iot-contrato-lora.md) |
| T4 | La firma HMAC cubre los bytes exactos de la trama con un contexto fijo; el gateway los reenvía sin modificar (Base64) | [contrato LoRa §2.3](../modules/iot-contrato-lora.md) |
| T5 | La verificación HMAC la hace el backend; el gateway es un relé | [arquitectura §3.2](../modules/iot-arquitectura.md) |
| T6 | Topics `agrotech/v1/gw/{gateway_uid}/up` y `/status`; ACL por gateway; el cuerpo no lleva `farm_id` ni `gateway_uid` | [contrato MQTT §2.1](../modules/iot-contrato-mqtt.md) |
| T7 | QoS 1, sesión persistente del consumidor y PUBACK manual después del `COMMIT` | [contrato MQTT §2.3](../modules/iot-contrato-mqtt.md) |
| T8 | Deduplicación por restricción `UNIQUE` en MariaDB; inserción atómica por trama | [persistencia §2.3](../modules/iot-persistencia-entrega.md) |
| T9 | Claves HMAC cifradas en reposo con clave maestra dedicada (no hash) | [seguridad §2.2](../modules/iot-seguridad.md) |
| T10 | Consumidor como proceso independiente de gunicorn | [arquitectura §3.2](../modules/iot-arquitectura.md) |
| T11 | IoT depende solo de `farms`; avicultura referenciará a IoT con asignaciones con vigencia | [arquitectura §3.6](../modules/iot-arquitectura.md) |
| T12 | Las mediciones no son `AuditEvent`; sí se auditan altas, rotaciones y revocaciones | [arquitectura §3.4](../modules/iot-arquitectura.md) |
| T13 | Rechazos permanentes se confirman y descartan; errores transitorios no se confirman | [contrato MQTT §2.5](../modules/iot-contrato-mqtt.md) |

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
| P-IOT-04 | Significado de "grupo de sensores" y si viaja en la trama | IoT-02 |
| P-IOT-05 | Parámetros de radio y cumplimiento normativo en Colombia | Firmware |
| P-IOT-06 | Estructura binaria de la trama, tamaño máximo y codificación de valores | IoT-04 |
| P-IOT-07 | Si el gateway verifica también la firma | Firmware |
| P-IOT-08 | ACK LoRa y su autenticación | Firmware |
| P-IOT-09 | Campo `uptime` u otra referencia temporal en la trama | IoT-04 |
| P-IOT-10 | Búfer del gateway: medio, capacidad, política al llenarse y ritmo de vaciado | Firmware |
| P-IOT-11 | Tolerancia entre `lora_rx_at` y `mqtt_rx_at` | IoT-04 |
| P-IOT-12 | Longitud de la etiqueta HMAC | IoT-04 |
| P-IOT-13 | Procedimiento ante borrado de NVS o reemplazo de placa | IoT-03 |
| P-IOT-14 | Tiempo casi real en React (consulta periódica o WebSocket) | IoT-07 |
| P-IOT-15 | Formato del mensaje `status` y *Last Will* | IoT-05 |
| P-IOT-16 | Agrupación de tramas al vaciar el búfer | IoT-05 |
| P-IOT-17 | Límites de cola y persistencia de Mosquitto | IoT-06 |
| P-IOT-18 | Versión MQTT 3.1.1 o 5 | IoT-05 |
| P-IOT-19 | Cuarentena de mensajes rechazados | IoT-05 |
| P-IOT-20 | Algoritmo y formato del cifrado en reposo de las claves HMAC | IoT-03 |
| P-IOT-21 | Procedimiento de carga de claves en el coordinador | IoT-03 |
| P-IOT-22 | Periodo de transición en rotaciones de clave | IoT-03 |
| P-IOT-23 | Cifrado de flash y arranque seguro del ESP32 | Firmware |
| P-IOT-24 | Autenticación de Mosquitto y certificados TLS en el ESP32 | IoT-06 |
| P-IOT-25 | ~~Coordinador ligado a uno o varios gateways~~ **Resuelto (aprobación IoT-02):** varios gateways autorizados de su misma finca | — |
| P-IOT-26 | Marca de agua por coordinador si existe retención | IoT-04 |
| P-IOT-27 | Modelo de deduplicación (opción A o B) | IoT-04 |
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

## 9. Brechas de implementación

No existen consumidor MQTT, configuración de Mosquitto, API REST, recepción de
mediciones ni firmware. IoT-02.1 y IoT-02.2 aportan los componentes de §10 y
§11.

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
