# IoT-01 — Contrato de mensajes MQTT

- **Etapa:** IoT-01 (solo documentación)
- **Estado de implementación:** El parser de payload, el consumidor MQTT y el
  comando Django existen en el repositorio. El contrato v1 y la configuración
  real del broker siguen sujetos a la validación operativa correspondiente.
  El consumidor delega en la ingesta Django; no llama a un Writer independiente.
- **Decisión asociada:** [DEC-13](../decisions/DEC-13-arquitectura-iot.md)
- **Fecha:** 2026-11-12

## 1. Propósito

Definir los topics, el formato de los mensajes, la calidad de servicio y las
reglas de validación entre el gateway, Mosquitto y el consumidor Django.

## 2. Especificación técnica

### 2.1 Topics

| Topic | Publica | Suscribe | Contenido |
|---|---|---|---|
| `agrotech/v1/gw/{gateway_uid}/up` | Gateway | Consumidor | Una trama LoRa reenviada |
| `agrotech/v1/gw/{gateway_uid}/status` | Gateway | Consumidor | Estado del gateway (conexión y desconexión) |

- `v1` es la versión del contrato de topics.
- El consumidor se suscribe con el comodín `agrotech/v1/gw/+/up` (`+` equivale a
  un nivel cualquiera del topic).
- El `gateway_uid` del topic es la **única** fuente de identidad del gateway.
  Mosquitto, mediante ACL, impide que un gateway publique en un topic que no
  sea el suyo (ver [seguridad](iot-seguridad.md)).
- Reservado y **fuera de la v1**: un topic de bajada (`down`) para comandos o
  configuración hacia el gateway.
- `status` puede usar el "último deseo" de MQTT (*Last Will*: mensaje que el
  broker publica solo si el gateway se desconecta sin avisar). Su formato es
  pendiente (P-IOT-15).

### 2.2 Mensaje `up` (borrador v1)

| Campo | Tipo | Obligatorio | Firmado por el coordinador | Descripción |
|---|---|---|---|---|
| `v` | entero | Sí | No | Versión del contrato del mensaje. |
| `frame` | texto Base64 | Sí | **Sí** (contiene la trama y su `tag`) | Bytes exactos recibidos por LoRa. |
| `lora_rx_at` | fecha ISO 8601 UTC o `null` | Sí | No | Hora de recepción LoRa según el gateway. |
| `clock_synced` | booleano | Sí | No | Si el reloj del gateway estaba sincronizado por NTP. |
| `buffered` | booleano | Sí | No | Si la trama pasó por el búfer local. |
| `rssi` | entero | No | No | Potencia de la señal recibida. |
| `snr` | decimal | No | No | Relación señal-ruido. |

Reglas:

- **No existe campo `farm_id` ni `gateway_uid` en el cuerpo.** Si aparecen
  campos no definidos, el mensaje se rechaza.
- Tamaño máximo del mensaje: a fijar con la trama (P-IOT-06). El consumidor
  rechaza lo que lo supere antes de decodificar.
- **Un mensaje = una trama [Aprobado, IoT-04.1].** No se agrupan tramas por
  mensaje en v1 (P-IOT-16 queda resuelto para v1).
- **`frame` [Aprobado]:** contiene los bytes completos de la trama LoRa
  (`signed_bytes` + etiqueta de 32 bytes), codificados en Base64, exactamente como
  los recibió el gateway: el gateway **no los altera**. El backend los
  decodificará y validará según el [contrato LoRa v1](iot-contrato-lora.md)
  (§2.7) cuando se implemente el consumidor (IoT-06). El alfabeto Base64 exacto
  (estándar con relleno) está pendiente de confirmar.
- **Tamaño máximo del mensaje [Pendiente de decisión]:** depende del tamaño de
  trama del contrato LoRa v1 (`25 + 5 × reading_count + 32` bytes; máximo
  provisional 137 bytes, sin validación física, P-06).
- Codificación UTF-8, JSON sin comentarios.

### 2.3 Calidad de servicio y sesión

- Gateway → broker: **QoS 1**, `retain = false` en `up`.
- Broker → consumidor: suscripción **QoS 1**.
- Consumidor:
  - Identificador de cliente MQTT fijo y **sesión persistente** (MQTT 3.1.1:
    `clean_session = false`; MQTT 5: `clean_start = false` con caducidad de
    sesión). Así Mosquitto guarda los mensajes mientras el consumidor está
    desconectado.
  - **Confirmación manual (recepción durable)**: el consumidor envía PUBACK
    solo cuando la recepción durable termina con `PERSISTED` (entrega nueva
    registrada) o `REDELIVERY` (la entrega ya estaba registrada y se reconoce
    como retransmisión). En ambos casos el servicio de recepción devuelve el
    resultado después del COMMIT en MariaDB, y el PUBACK se envía después.
    No espera al procesamiento del worker.
  - Con `PERSISTENCE_ERROR`, cualquier otro resultado o una excepción, no se
    envía PUBACK: el consumidor deja de leer, se desconecta y, tras comprobar
    MariaDB, se reconecta para que la sesión persistente reentregue el mensaje.
  - `ACCEPTED`, `DUPLICATE`, `REJECTED`, `CONFLICT` y demás estados finales son
    resultados del **procesamiento posterior** del worker durable sobre la
    entrega ya registrada; no son condiciones previas del PUBACK.
- Mosquitto debe tener persistencia activa para no perder la cola al
  reiniciarse. Límites de cola y caducidad: configuración operativa, fuera de
  IoT-01 (P-IOT-17).
- El consumidor del repositorio usa MQTT 5 (BUILD-FLEPSI-02): `clean_start =
  false`, `SessionExpiryInterval` explícito (`MQTT_SESSION_EXPIRY_SECONDS`,
  86 400 s por defecto), suscripción QoS 1 y PUBACK manual después de la
  persistencia durable. Validado localmente con Mosquitto 2.1.2
  (BUILD-FLEPSI-03); la configuración efectiva del broker de producción
  (Flespi) y de sus sesiones aún requiere validación operativa (P-IOT-17/18).

### 2.4 Implicaciones de QoS 1

QoS 1 garantiza "al menos una vez". Hay duplicados cuando:

1. El gateway publica, el broker guarda, pero el PUBACK no llega al gateway: el
   gateway reenvía (marca DUP) y el broker puede tener dos copias.
2. El consumidor guarda pero cae antes de su PUBACK: el broker reenvía.
3. El gateway reenvía tramas del búfer cuya publicación no llegó a confirmarse.
4. El coordinador reintenta por radio (si existe ACK LoRa).

En todos los casos la copia trae el **mismo `frame`**, así que la clave
(`coordinator_uid`, `boot_counter`, `seq`) la identifica. Por eso no hace falta
QoS 2 (exactamente una vez), que es más costoso y no cubre los casos 3 y 4.

### 2.5 Resultados del consumidor

El flujo tiene dos fases con resultados distintos. El PUBACK depende **solo**
de la primera.

**A. Recepción durable (consumidor).** `MqttConsumer.handle_message`
(`apps/iot/mqtt/consumer.py`) llama a `register_encrypted_mqtt_delivery`, que
delega en `register_mqtt_delivery` (`apps/iot/services/mqtt_delivery_receipts.py`).
El servicio devuelve un `MqttReceiptStatus` después de cerrar su transacción.

**B. Procesamiento posterior (worker durable).** `run_mqtt_delivery_worker_once`
(`apps/iot/services/mqtt_delivery_worker.py`) reclama entregas ya registradas y
las procesa (`apps/iot/services/mqtt_delivery_processing.py`). El resultado se
guarda como estado de la entrega (`IotMqttDeliveryState`). Ocurre después del
PUBACK y no lo condiciona.

| Fase | Resultado | Significado | ¿PUBACK? | Componente |
|---|---|---|---|---|
| Recepción | `PERSISTED` | Entrega nueva registrada; el servicio devuelve el resultado tras el COMMIT. | Sí, después del retorno. | Consumidor + `register_mqtt_delivery` |
| Recepción | `REDELIVERY` | La `transport_key` ya estaba registrada; se reconoce como retransmisión (evento `REDELIVERY`). | Sí, después del retorno. | Consumidor + `register_mqtt_delivery` |
| Recepción | `IDENTITY_INCONSISTENCY` | Misma `transport_key` con metadatos de transporte distintos (topic, QoS, tamaño o SHA-256); se registra el evento `IDENTITY_INCONSISTENCY`. | No. | Consumidor + `register_mqtt_delivery` |
| Recepción | `PERSISTENCE_ERROR` | Error de base de datos; la transacción se revierte (solo el errno 1213 se reintenta de forma acotada antes, ver `iot-persistencia-entrega.md`). | No. | Consumidor + `register_mqtt_delivery` |
| Procesamiento | `ACCEPTED` | La ingesta aceptó la trama. | No aplica (ya confirmado). | Worker |
| Procesamiento | `DUPLICATE` | La ingesta encontró la misma identidad de trama ya aceptada. | No aplica. | Worker |
| Procesamiento | `CONFLICT` | La identidad de trama existe con contenido distinto. | No aplica. | Worker |
| Procesamiento | `REJECTED` | La ingesta rechazó la trama (`REJECTED_*`: payload, gateway, autorización, credencial, HMAC, generación, replay o mapeo de sensor). | No aplica. | Worker |
| Procesamiento | `RETRY_PENDING` | Fallo transitorio con intentos disponibles; se reintenta con espera exponencial. | No aplica. | Worker (`mqtt_delivery_failures.py`) |
| Procesamiento | `DEAD_LETTER` | Fallo permanente o intentos agotados; bloquea su carril FIFO hasta una acción explícita del operador. | No aplica. | Worker (`mqtt_delivery_failures.py`) |
| Operación | `RESOLVED` | Entrega resuelta por una acción auditada del operador. | No aplica. | `mqtt_delivery_ops.py` |

Notas:

- Con cualquier resultado de recepción distinto de `PERSISTED` o
  `REDELIVERY`, o con una excepción, `MqttConsumer._on_message` no confirma,
  deja de leer y se desconecta. `run()` espera, comprueba MariaDB y reconecta;
  la sesión persistente reentrega el mensaje. Tras
  `MQTT_MAX_PERSISTENCE_FAILURES` fallos consecutivos el proceso termina con
  error.
- `ACCEPTED`, `DUPLICATE`, `CONFLICT` y `REJECTED` provienen de
  `_state_for_ingestion_result`, que traduce el `IngestionStatus` de la
  ingesta. Un `AppError` solo se convierte en denegación de autorización
  cuando su código es 403; otros códigos se propagan como errores.
- Los fallos del procesamiento se clasifican en `classify_failure`
  (`mqtt_delivery_failures.py`): errores de integridad, de contrato o de
  entrega inexistente son permanentes (`DEAD_LETTER`); otros errores de base
  de datos, de configuración o inesperados son transitorios (`RETRY_PENDING`
  hasta agotar intentos). La clave de payload no disponible o un fallo de
  autenticación del payload pausan el procesamiento de forma global.
- `TRANSIENT_ERROR` y `OPERATIONAL_ERROR` no son estados actuales: pertenecen
  a `apps.iot.mqtt.delivery` (`DeliveryClassification`, B2.1-02), un módulo
  histórico que solo importan las pruebas (`test_mqtt_consumer.py`) y que no
  participa en el flujo de producción.

Si el envío local de PUBACK falla, el consumidor registra el error y no marca
el mensaje como confirmado. No se reintenta ciegamente el ACK: ante resultado
ambiguo se deja que MQTT vuelva a entregar y la idempotencia existente resuelva
la duplicación.

### 2.6 Reintentos: valores implementados y decisión pendiente

El worker durable, los leases con fencing, el backoff, la DLQ y la resolución
operativa **están implementados** en `apps/iot/services/mqtt_delivery_*.py` y en
los comandos `mqtt_consumer`, `mqtt_delivery_worker` y `mqtt_deliveries`.

Valores por defecto vigentes en el código:

| Parámetro | Valor por defecto | Configuración |
|---|---|---|
| Intentos antes de `DEAD_LETTER` | 8 | `IOT_MQTT_DELIVERY_MAX_ATTEMPTS` o `--max-attempts` |
| Espera base | 5 s | `IOT_MQTT_DELIVERY_RETRY_BASE_SECONDS` o `--retry-base-seconds` |
| Espera máxima | 900 s | `IOT_MQTT_DELIVERY_RETRY_MAX_SECONDS` o `--retry-max-seconds` |
| Crecimiento | `base × 2**(intento-1)`, acotado | — |
| Jitter | No implementado | — |
| Lease de procesamiento | 60 s, renovable | `apps/iot/services/mqtt_delivery_leases.py` |

Los valores aprobados en una fase anterior (tres reintentos, espera inicial de
un segundo, máximo de 30 s y jitter de ±20 %) **no** son los valores actuales
del código. La diferencia queda registrada como decisión abierta: o se ajusta la
configuración por entorno, o se modifica el código en una fase autorizada.

Sigue **no implementado**: la retención de 30 días para rechazos y entregas
resueltas. Las entregas pendientes y en `DEAD_LETTER` se conservan hasta una
resolución explícita del operador.

## 3. Explicación sencilla

Cada gateway tiene su propio "buzón" (topic) y solo puede escribir en el suyo.
El mensaje lleva el paquete original del coordinador tal cual, más datos de
cómo se recibió. El backend sabe de qué gateway viene por el buzón, no por lo
que diga el mensaje. MQTT con QoS 1 puede entregar el mismo mensaje dos veces;
el backend lo sabe y lo tolera.

## 4. Ejemplo práctico (ilustrativo)

Topic: `agrotech/v1/gw/G-A/up`

```json
{
  "v": 1,
  "frame": "<bytes de la trama en Base64>",
  "lora_rx_at": "2026-11-12T14:03:22Z",
  "clock_synced": true,
  "buffered": false,
  "rssi": -87,
  "snr": 7.5
}
```

Mensaje rechazado por contener un campo no permitido:

```json
{ "v": 1, "frame": "...", "farm_id": 99, "lora_rx_at": null, "clock_synced": false, "buffered": true }
```

## 5. Decisiones aprobadas

- Eclipse Mosquitto como broker.
- React no usa MQTT.
- Entrega QoS 1 con deduplicación en el backend.

## 6. Aspectos pendientes

- P-IOT-06 Tamaño máximo del mensaje: pendiente de decisión (depende de P-06,
  provisional, del contrato LoRa v1).
- P-IOT-15 Formato del mensaje `status` y uso de *Last Will*.
- ~~P-IOT-16 Agrupación de tramas~~: resuelto para v1 (una trama por mensaje).
- Variante exacta de Base64 del campo `frame`.
- P-IOT-17 Límites de cola y persistencia de Mosquitto.
- P-IOT-18 Versión de MQTT (3.1.1 o 5).
- P-IOT-19 Cuarentena de mensajes rechazados.

## 7. Riesgos y consideraciones

- `lora_rx_at`, `rssi` y `snr` los declara el gateway y no están firmados por el
  coordinador: un gateway comprometido podría falsearlos, pero no las lecturas
  (R-IOT-04).
- Si el consumidor no confirma por un error permanente mal clasificado como
  transitorio, la cola se bloquea (R-IOT-07).
