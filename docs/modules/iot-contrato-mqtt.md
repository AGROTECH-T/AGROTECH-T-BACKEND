# IoT-01 — Contrato de mensajes MQTT

- **Etapa:** IoT-01 (solo documentación)
- **Estado de implementación:** Diseñada. Contrato **v1 en borrador**; se
  congelará antes de implementar el consumidor.
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
- Un mensaje = una trama. Agrupar varias tramas por mensaje al vaciar el búfer
  queda pendiente (P-IOT-16) y solo se justificaría si el volumen lo exige.
- Codificación UTF-8, JSON sin comentarios.

### 2.3 Calidad de servicio y sesión

- Gateway → broker: **QoS 1**, `retain = false` en `up`.
- Broker → consumidor: suscripción **QoS 1**.
- Consumidor:
  - Identificador de cliente MQTT fijo y **sesión persistente** (MQTT 3.1.1:
    `clean_session = false`; MQTT 5: `clean_start = false` con caducidad de
    sesión). Así Mosquitto guarda los mensajes mientras el consumidor está
    desconectado.
  - **Confirmación manual**: el PUBACK se envía después del `COMMIT` en MariaDB.
    La biblioteca cliente que se elija en IoT-05 debe permitirlo.
- Mosquitto debe tener persistencia activa para no perder la cola al
  reiniciarse. Límites de cola y caducidad: configuración operativa, fuera de
  IoT-01 (P-IOT-17).
- Versión del protocolo (3.1.1 o 5) según lo que soporte la biblioteca del
  firmware del gateway: pendiente (P-IOT-18).

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

| Resultado | Acción | PUBACK |
|---|---|---|
| Guardado | `COMMIT` | Sí, después del commit |
| Duplicado | Nada que insertar | Sí |
| Rechazo permanente (formato, firma, dispositivo desconocido o de otra finca, gateway revocado) | Registro de log y contador | Sí, para no repetirlo indefinidamente |
| Error transitorio (base de datos caída, tiempo agotado) | Reintento con espera creciente | **No** |

Los rechazos permanentes no se reprocesan. Una tabla de cuarentena para
revisarlos queda pendiente (P-IOT-19) y no se crea por defecto.

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

- P-IOT-06 Tamaño máximo del mensaje.
- P-IOT-15 Formato del mensaje `status` y uso de *Last Will*.
- P-IOT-16 Agrupación de tramas al vaciar el búfer.
- P-IOT-17 Límites de cola y persistencia de Mosquitto.
- P-IOT-18 Versión de MQTT (3.1.1 o 5).
- P-IOT-19 Cuarentena de mensajes rechazados.

## 7. Riesgos y consideraciones

- `lora_rx_at`, `rssi` y `snr` los declara el gateway y no están firmados por el
  coordinador: un gateway comprometido podría falsearlos, pero no las lecturas
  (R-IOT-04).
- Si el consumidor no confirma por un error permanente mal clasificado como
  transitorio, la cola se bloquea (R-IOT-07).
