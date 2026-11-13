# IoT-01 — Arquitectura general del sistema IoT

- **Etapa:** IoT-01 (solo documentación)
- **Estado de implementación:** Diseñada. No existe código IoT: `apps/iot/` es un esqueleto.
- **Decisión asociada:** [DEC-13](../decisions/DEC-13-arquitectura-iot.md)
- **Fecha:** 2026-11-12
- **Documentos relacionados:**
  [flujo de una medición](iot-flujo-medicion.md) ·
  [contrato LoRa](iot-contrato-lora.md) ·
  [contrato MQTT](iot-contrato-mqtt.md) ·
  [seguridad](iot-seguridad.md) ·
  [persistencia y entrega](iot-persistencia-entrega.md)

## 1. Propósito

Definir los componentes del sistema IoT de AGROTECH-T, la responsabilidad de
cada uno, las fronteras de confianza y cómo se integra con la arquitectura
modular existente (monolito Django con dominios en `apps/`).

## 2. Glosario

| Término | Significado |
|---|---|
| **IoT** | *Internet of Things*: dispositivos físicos que miden y envían datos por red. |
| **ESP32** | Microcontrolador con Wi-Fi integrado usado en el coordinador y en el gateway. |
| **LoRa** | Modulación de radio de largo alcance y bajo consumo. Aquí se usa punto a punto con módulos SX1278 en 433 MHz, **sin LoRaWAN** (sin servidor de red ni seguridad propia del protocolo). |
| **Coordinador** | ESP32 #1. Lee los sensores conectados por cable y emite tramas LoRa firmadas. |
| **Gateway** | ESP32 #2. Recibe tramas LoRa y las reenvía por Wi-Fi a MQTT. |
| **Trama** | Un paquete LoRa individual emitido por el coordinador. |
| **MQTT** | Protocolo de mensajería publicación/suscripción sobre TCP, diseñado para dispositivos. |
| **Broker** | Servidor MQTT que recibe publicaciones y las entrega a los suscriptores. Aquí: Eclipse Mosquitto. |
| **Topic** | "Dirección" jerárquica de un mensaje MQTT, por ejemplo `agrotech/v1/gw/<id>/up`. |
| **QoS 1** | Nivel de calidad MQTT "al menos una vez": el mensaje llega, pero puede llegar repetido. |
| **HMAC-SHA256** | Firma con clave secreta compartida. Prueba quién envió un mensaje y que no fue alterado. |
| **Consumidor** | Proceso Django independiente que se suscribe al broker y guarda las mediciones. |
| **Idempotencia** | Procesar el mismo mensaje varias veces produce el mismo resultado que procesarlo una vez. |
| **Deduplicación** | Detectar y descartar copias repetidas de un mismo mensaje. |

## 3. Especificación técnica

### 3.1 Cadena de componentes

```
Sensores ──cable──▶ Coordinador ESP32 ──LoRa 433 MHz──▶ Gateway ESP32
                     (firma HMAC)                       (búfer local)
                                                             │ Wi-Fi + TLS
                                                             ▼
React (web) ◀──REST/HTTPS── Django API ◀── MariaDB ◀── Consumidor ◀── Mosquitto
                                                     (proceso aparte)  (QoS 1)
```

### 3.2 Responsabilidades

| Componente | Hace | No hace |
|---|---|---|
| Coordinador | Lee sensores, numera tramas (arranque + secuencia), firma con su clave HMAC, transmite por LoRa. | No conoce la finca ni el broker. No usa Wi-Fi. |
| Gateway | Recibe tramas, las guarda en un búfer si no hay Wi-Fi, las publica **sin modificarlas** en su topic MQTT, agrega metadatos de radio y de hora de recepción. | No interpreta las lecturas. No verifica la firma (ver [seguridad](iot-seguridad.md) §P-IOT-07). No decide la finca. |
| Mosquitto | Autentica a cada gateway, aplica ACL por topic, conserva mensajes QoS 1 para el consumidor. | No valida contenido de negocio. |
| Consumidor Django | Valida, verifica HMAC, resuelve finca, deduplica, persiste en una transacción y confirma al broker. | No atiende HTTP. No corre dentro de gunicorn. |
| MariaDB | Única base de datos del módulo IoT. | — |
| API REST Django | Expone dispositivos, sensores e historial con la autenticación y membresías existentes. | No recibe telemetría de dispositivos. |
| React | Consulta la API REST como aplicación web. | **No usa MQTT** ni se conecta al broker. |

### 3.3 Fronteras de confianza

1. **Radio LoRa (no confiable):** cualquiera con un radio de 433 MHz puede
   transmitir o escuchar. La autenticidad se garantiza solo con la firma HMAC
   del coordinador. Las lecturas **no van cifradas** (HMAC no oculta datos).
2. **Gateway → broker (semiconfiable):** autenticado con credenciales propias
   del gateway y TLS. Los metadatos que agrega el gateway (RSSI, hora de
   recepción) solo están respaldados por esa credencial, no por la firma del
   coordinador.
3. **Broker → consumidor → MariaDB (confiable):** red e infraestructura
   controladas por la plataforma.

### 3.4 Integración con el backend existente

- Dominio: `apps/iot/` (ya registrado en `INSTALLED_APPS`, `config/settings.py`).
- Aislamiento por finca: toda entidad IoT raíz tendrá FK a `farms.Farm`,
  siguiendo el patrón de `apps/inventario/models.py` (`Product.farm`).
- Lectura humana: membresía activa en finca activa
  (`apps.farms.services.require_active_membership`). Escritura administrativa:
  `lock_active_membership` dentro de `transaction.atomic()` y roles definidos en
  `apps/farms/capabilities.py`, igual que inventario.
- Auditoría: altas, revocaciones y rotaciones de dispositivos y claves irán a
  `apps.trazabilidad` (`record_audit_event`). **Las mediciones no son eventos de
  auditoría**: van en tablas propias de IoT.
- El consumidor no tiene usuario humano: deriva la finca del gateway
  registrado, nunca del contenido del mensaje (ver
  [seguridad](iot-seguridad.md) §3.4).

### 3.5 Identificadores

| Entidad | Identificador | Origen |
|---|---|---|
| Gateway | `gateway_uid` | Lo genera el backend al registrar el gateway; también es su usuario MQTT. |
| Coordinador | `coordinator_uid` | Lo genera el backend al registrar el coordinador; viaja dentro de la trama firmada. |
| Sensor | (`coordinator_uid`, `canal`) | Canal local del coordinador; el backend lo asocia a un sensor registrado. |
| Arranque | `boot_counter` | Contador persistente del coordinador, sube en cada reinicio. |
| Trama | (`coordinator_uid`, `boot_counter`, `seq`) | Identidad única de cada trama; base de la deduplicación. |

La MAC del ESP32 **no** se usa como identidad: puede falsificarse y cambia si se
reemplaza la placa. Puede registrarse como dato informativo. El formato exacto
de los identificadores (longitud, alfabeto) queda pendiente (P-IOT-03).

### 3.6 Relación futura con avicultura

`apps/production/avicultura` todavía no tiene modelo de galpón. Para no
depender de él:

- IoT solo depende de `farms`. Un coordinador o sensor puede tener una
  **etiqueta de ubicación de texto libre** opcional ("Galpón norte", por ejemplo)
  sin valor de autorización.
- Cuando exista el galpón, **avicultura referenciará a IoT** (no al revés)
  mediante una asignación con vigencia (`desde` / `hasta`) entre un galpón y un
  coordinador o sensor.
- El historial de un galpón se obtendrá cruzando las mediciones con los
  periodos de asignación. Así, mover un coordinador de galpón no reescribe
  mediciones antiguas.
- Esto respeta la regla de `apps/production/avicultura/apps.py`: cada dominio se
  comunica por servicios, sin tocar estructuras ajenas.

## 4. Explicación sencilla

El coordinador es quien mide. Pone un "sello" secreto (la firma HMAC) en cada
paquete para que nadie pueda inventar lecturas. El gateway es un cartero: no
abre los sobres, solo los lleva hasta el broker, y si no hay Wi-Fi los guarda
hasta que vuelva. El broker es la oficina de correos. El consumidor Django
abre cada sobre, comprueba el sello, mira de qué finca es el gateway que lo
trajo, descarta los repetidos y lo guarda en MariaDB. React no habla con los
aparatos: solo pregunta a la API como hace hoy con inventario.

## 5. Ejemplo práctico

Una finca con un coordinador y un gateway:

1. El coordinador `coordinator_uid = C-A` lee 2 sensores (canales 1 y 2).
2. Emite la trama `(C-A, arranque 7, seq 120)` firmada con la clave de `C-A`.
3. El gateway `G-A` la recibe y publica en `agrotech/v1/gw/G-A/up`.
4. El consumidor verifica: `G-A` está registrado en la finca 12, `C-A` también
   pertenece a la finca 12, la firma es válida y `(C-A, 7, 120)` no existía.
5. Guarda dos mediciones en la finca 12 y confirma el mensaje al broker.

## 6. Decisiones aprobadas

- Cadena Sensores → coordinador → LoRa → gateway → Wi-Fi → MQTT → Mosquitto →
  Django → MariaDB → React.
- LoRa SX1278 en 433 MHz.
- HMAC-SHA256 con clave propia de cada coordinador.
- MariaDB como única base de datos del módulo IoT.
- Eclipse Mosquitto como broker.
- React sigue siendo aplicación web, sin MQTT en los equipos de los usuarios.
- Primera versión: sensores cableados al coordinador, en uno o dos grupos.

## 7. Aspectos pendientes

Ver el registro completo en [DEC-13 §6](../decisions/DEC-13-arquitectura-iot.md).
Los más relevantes para esta vista general:

- P-IOT-01 Ubicación del broker y del backend (en la finca o en la nube).
- P-IOT-02 Cómo se impone "solo MariaDB" frente a la rama Postgres de `config/database.py`.
- P-IOT-03 Formato de los identificadores.
- P-IOT-04 Significado exacto de "grupo de sensores" y si viaja en la trama.
- P-IOT-14 Tiempo casi real en React (consulta periódica o WebSocket).

## 8. Riesgos y consideraciones

- Sin LoRaWAN no hay cifrado de radio: las lecturas pueden escucharse (R-IOT-01).
- El consumidor es un proceso nuevo: hoy el `Dockerfile` solo ejecuta
  `migrate` y gunicorn (R-IOT-06).
- No se deben usar las mediciones como `AuditEvent`: el volumen lo haría
  inviable (R-IOT-08).
