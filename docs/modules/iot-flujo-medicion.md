# IoT-01 — Flujo completo de una medición

- **Etapa:** IoT-01 (solo documentación)
- **Estado de implementación:** Parcial. Están implementados el parser Frame v1,
  validación MQTT/Base64, consumidor, servicio de ingesta transaccional y consulta
  REST. Su operación conectada no se verificó en este BUILD.
- **Decisión asociada:** [DEC-13](../decisions/DEC-13-arquitectura-iot.md)
- **Fecha:** 2026-11-12 (actualizado el 2026-11-13, IoT-04.2)

## 1. Propósito

Describir paso a paso el recorrido de una lectura desde el sensor hasta la
pantalla, y distinguir con precisión **tres momentos que no son lo mismo**:
recepción LoRa, recepción MQTT y persistencia en MariaDB.

## 2. Especificación técnica

### 2.1 Etapas y responsables

| # | Etapa | Dónde | Resultado | Confirmación |
|---|---|---|---|---|
| 1 | Lectura | Coordinador | Valores por canal | — |
| 2 | Armado y firma | Coordinador | Trama con `coordinator_uid`, `key_version`, `boot_counter`, `seq`, lecturas y etiqueta HMAC | — |
| 3 | Transmisión LoRa | Radio | Trama en el aire | ACK LoRa opcional (P-IOT-08) |
| 4 | **Recepción LoRa** | Gateway | Trama íntegra en memoria + `lora_rx_at`, RSSI, SNR | — |
| 5 | Búfer local | Gateway | Trama en cola si no hay Wi-Fi o broker | — |
| 6 | Publicación MQTT QoS 1 | Gateway → Mosquitto | Mensaje en el broker | `PUBACK` del broker al gateway |
| 7 | Entrega QoS 1 | Mosquitto → consumidor | **Recepción MQTT** en el consumidor (`mqtt_rx_at`) | Pendiente hasta el paso 10 |
| 8 | Validación | Consumidor Django → `apps/iot/services/ingestion.py` | Mensaje aceptado o rechazado | — |
| 9 | **Persistencia** | Servicio de ingesta Django → MariaDB | `COMMIT` de la trama y mediciones en la transacción existente | — |
| 10 | Confirmación al broker | Consumidor → Mosquitto | `PUBACK` del consumidor | El broker deja de reenviar |
| 11 | Consulta | React → API REST | Historial filtrado por finca | — |

**PUBACK:** mensaje con el que MQTT confirma un envío QoS 1. Mientras no
llega, el emisor reenvía.

### 2.2 Los tres momentos

- **Recepción LoRa (`lora_rx_at`):** el gateway tiene la trama. Puede perderse
  todavía si el gateway se reinicia sin búfer persistente (P-IOT-10).
- **Recepción MQTT (`mqtt_rx_at`):** el consumidor recibió el mensaje del
  broker. Todavía **no** está guardado. Si el consumidor cae aquí, el broker lo
  reenvía porque no hubo PUBACK.
- **Persistencia (`persisted_at`):** la transacción en MariaDB terminó con
  `COMMIT`. **Solo desde aquí la medición existe para el sistema.** Solo después
  el consumidor envía el PUBACK.

El servicio de ingesta Django es el único escritor de ingesta IoT en MariaDB;
el consumidor MQTT delega en él. No existe comunicación con un Writer externo.

Regla: **primero `COMMIT`, después PUBACK**. Al revés se perderían datos si el
proceso cae entre ambos pasos. El costo es que pueden llegar duplicados, que la
deduplicación absorbe (ver [persistencia y entrega](iot-persistencia-entrega.md)).

### 2.3 Hora de la medición

El coordinador no tiene reloj de tiempo real confirmado. Propuesta:

| Campo | Fuente | Confianza |
|---|---|---|
| `measured_at` | `lora_rx_at` del gateway si su reloj está sincronizado por NTP; si no, `mqtt_rx_at` marcado como aproximado | Media |
| `lora_rx_at` | Reloj del gateway (dato declarado por el gateway, no firmado por el coordinador) | Media |
| `mqtt_rx_at` | Reloj del servidor del consumidor | Alta |
| `persisted_at` | Reloj del servidor de base de datos | Alta |

**NTP:** protocolo con el que un equipo ajusta su reloj por Internet. El
gateway informa en cada mensaje si su reloj estaba sincronizado
(`clock_synced`). Si el coordinador incluyera su tiempo desde el arranque
(`uptime`), la precisión mejoraría para tramas guardadas en búfer; queda
pendiente (P-IOT-09).

### 2.4 Validaciones del consumidor (orden)

1. Tamaño máximo y JSON válido.
2. Versión del contrato MQTT soportada.
3. `gateway_uid` tomado **del topic**, registrado y activo.
4. Finca del gateway activa.
5. Trama decodificable y versión de trama soportada. El consumidor valida el
   JSON/Base64 y `parse_lora_frame` valida la estructura binaria v1
   (`apps/iot/lora_frame.py`;
   [contrato LoRa §2.8](iot-contrato-lora.md)): tipo, `frame_version` = 1,
   `reading_count` entre 1 y 16 (provisional), longitud exacta
   `25 + 5 × reading_count + 32`, uid canónico, `key_version` ≥ 1, canales entre
   1 y 65535 y sin repetir, y `metric_code` conocido.
6. `coordinator_uid` registrado, activo y de **la misma finca** que el gateway.
7. Firma HMAC válida con la clave vigente del coordinador.
8. Deduplicación por (`coordinator_id`, `credential_id`, `boot_counter`, `seq`),
   mediante la restricción única de `ReceivedFrame` y el flujo de ingesta Django.
9. Cada canal corresponde a un sensor registrado con la misma métrica; si no,
   se rechaza la trama entera. Si el sensor está registrado pero desactivado, se
   acepta la trama y se omite solo esa lectura (IoT-04.3-8,
   [persistencia §2.6.5](iot-persistencia-entrega.md)). Los rangos físicos y la
   política de centinelas siguen pendientes (P-IOT-28).
10. Inserción de la trama y de todas sus mediciones válidas en **una**
    transacción de `apps/iot/services/ingestion.py`.

Los pasos 1 a 7 son baratos de comprobar y no consultan datos de otra finca, por
lo que se ejecutan antes de cualquier escritura. Este es el orden lógico de las
validaciones; el orden de bloqueos de la transacción de ingestión aprobada en
IoT-04.3-A (Farm → Coordinator → Gateway → CoordinatorGateway → Credential →
BootWindow → Sensors) está en [persistencia §2.6.3](iot-persistencia-entrega.md).

## 3. Explicación sencilla

El gateway "tener" el paquete no significa que esté guardado. Que el consumidor
lo "reciba" tampoco. Solo cuenta cuando MariaDB lo guardó. Por eso el
consumidor le dice al broker "ya está" únicamente después de guardar. Si algo
falla antes, el broker se lo vuelve a mandar, y como cada trama tiene un número
único, el sistema reconoce la repetida y no la guarda dos veces.

## 4. Ejemplo práctico

Corte de energía en el servidor durante el guardado:

1. El consumidor recibe la trama `(C-A, 7, 120)`.
2. Inicia la transacción, pero el servidor se apaga antes del `COMMIT`.
3. MariaDB deshace la transacción: no queda nada a medias.
4. No hubo PUBACK, así que al reconectar Mosquitto reenvía el mensaje.
5. El consumidor lo procesa de nuevo y esta vez guarda y confirma.

Duplicado normal por QoS 1:

1. El consumidor guarda `(C-A, 7, 120)` y hace `COMMIT`.
2. La conexión cae antes de enviar el PUBACK.
3. Mosquitto reenvía. La deduplicación detecta que ya existe, no inserta nada y
   envía el PUBACK.

## 5. Decisiones aprobadas

- MariaDB es el punto de verdad: una medición existe solo tras el `COMMIT`.
- Entrega MQTT con QoS 1 (al menos una vez) + deduplicación en el backend.

## 6. Aspectos pendientes

- P-IOT-08 ACK LoRa del gateway al coordinador.
- P-IOT-09 `uptime` u otra referencia temporal en la trama.
- P-IOT-10 Búfer del gateway en RAM o en flash, capacidad y política al llenarse.
- P-IOT-11 Tolerancia máxima entre `lora_rx_at` y `mqtt_rx_at` antes de
  marcar una medición como sospechosa.

## 7. Riesgos y consideraciones

- Tramas en búfer con el reloj del gateway sin sincronizar tendrán hora
  aproximada (R-IOT-05).
- Un error permanente de base de datos detiene la confirmación y el broker
  acumula mensajes: el consumidor debe pausar y reintentar con espera
  creciente, no descartar (R-IOT-07).
