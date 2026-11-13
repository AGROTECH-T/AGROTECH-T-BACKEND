# DEC-13 — Arquitectura y contratos técnicos del módulo IoT

- **Identificador:** DEC-13
- **Título:** Arquitectura y contratos técnicos del módulo IoT
- **Estado de decisión:** Aprobada (parcial: los puntos de §6 siguen pendientes)
- **Estado de implementación:** Diseñada. Sin código.
- **Alcance:** BUILD IoT-01
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
| P-IOT-02 | Cómo imponer "solo MariaDB" frente a la rama Postgres de `config/database.py` | IoT-02 |
| P-IOT-03 | Formato de `gateway_uid` y `coordinator_uid` | IoT-02 |
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
| P-IOT-25 | Coordinador ligado a un gateway o a cualquier gateway de su finca | IoT-02 |
| P-IOT-26 | Marca de agua por coordinador si existe retención | IoT-04 |
| P-IOT-27 | Modelo de deduplicación (opción A o B) | IoT-04 |
| P-IOT-28 | Tipos de métrica, unidades, rangos y precisión | IoT-02 |
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

Todo: no existen modelos, servicios, consumidor, configuración de Mosquitto ni
firmware. La implementación empieza en IoT-02 con autorización.
