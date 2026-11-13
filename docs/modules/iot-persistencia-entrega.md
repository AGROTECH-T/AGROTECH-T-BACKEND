# IoT-01 — Persistencia, confirmaciones, reintentos y deduplicación

- **Etapa:** IoT-01 (solo documentación)
- **Estado de implementación:** Diseñada. Los modelos se definirán en IoT-02.
- **Decisión asociada:** [DEC-13](../decisions/DEC-13-arquitectura-iot.md)
- **Fecha:** 2026-11-12

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
| Consumidor → MariaDB | Transacción (`transaction.atomic()`) | `ROLLBACK`, sin PUBACK y reintento con espera creciente. |

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

- Clave de identidad de una trama: (`coordinator_uid`, `boot_counter`, `seq`).
- **Autoridad:** una restricción `UNIQUE` en MariaDB, no una comprobación
  previa en Python. Una consulta previa sola tiene condición de carrera: dos
  copias simultáneas podrían pasar la comprobación a la vez.
- Todas las lecturas de una trama se insertan en **una sola transacción**: o se
  guardan todas o ninguna. Si la inserción choca con la restricción única, la
  trama es un duplicado y se responde con éxito (idempotencia).
- Opciones de modelo para IoT-02 (decisión pendiente, P-IOT-27):
  - **A.** Restricción única en la medición: (`sensor`, `boot_counter`, `seq`).
    Una sola tabla.
  - **B.** Tabla de tramas recibidas con restricción única
    (`coordinator`, `boot_counter`, `seq`) y mediciones como hijas. Permite
    guardar los metadatos de recepción una sola vez por trama.
  - Recomendación preliminar: **B**, porque `lora_rx_at`, `rssi`, `snr`,
    `buffered` y `gateway` son de la trama, no de cada lectura, y evita
    repetirlos en cada fila.
- No se usa SQL específico (como `INSERT IGNORE`) fuera del ORM sin justificarlo:
  se captura `IntegrityError` dentro de un punto de guardado (*savepoint*).

### 2.4 Persistencia en MariaDB

- Motor InnoDB, `utf8mb4`, igual que el resto del proyecto
  (`config/database.py`).
- Cada fila IoT de telemetría lleva `farm_id`, desnormalizado (repetido a
  propósito): permite filtrar y crear índices por finca sin cruzar varias
  tablas.
- Índice previsto para el historial: (`farm`, `sensor`, `measured_at`).
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
  finca inactiva, coordinador de otra finca, firma inválida, canal sin sensor,
  valor fuera de rango.
- **Transitorios** (no confirmar, reintentar): base de datos no disponible,
  bloqueo agotado, conexión perdida.
- Si un error no se puede clasificar, se trata como transitorio con un número
  máximo de reintentos y luego como permanente con registro, para que un
  mensaje defectuoso no bloquee la cola (valor pendiente).
- Proceso largo: cerrar conexiones de base de datos viejas entre mensajes
  (`django.db.close_old_connections`).

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
- QoS 1 con deduplicación por (`coordinator_uid`, `boot_counter`, `seq`).

## 6. Aspectos pendientes

- P-IOT-08 ACK LoRa.
- P-IOT-10 Medio, capacidad y política del búfer del gateway.
- P-IOT-27 Modelo de deduplicación (opción A o B).
- P-IOT-28 Métricas, unidades y precisión.
- P-IOT-29 Retención y agregación.

## 7. Riesgos y consideraciones

- Pérdida de tramas si el búfer es en RAM y el gateway se reinicia (R-IOT-05).
- Crecimiento continuo de la tabla de mediciones (R-IOT-08).
- `config/database.py` puede seleccionar Postgres si existe `DATABASE_URL`; IoT
  está aprobado solo para MariaDB y debe definirse cómo se impone (R-IOT-11,
  P-IOT-02).
