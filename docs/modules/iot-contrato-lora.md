# IoT-01 — Contrato de comunicación LoRa

- **Etapa:** IoT-01 (solo documentación)
- **Estado de implementación:** Contrato binario v1 aprobado (IoT-04.1, §2.7),
  con pendientes físicos y operativos (tamaño máximo provisional, rango de
  temperatura, centinelas, validación en hardware). **Parser v1 implementado en
  IoT-04.2** (§2.8). Sin persistencia, ingestión ni consumidor MQTT.
- **Decisión asociada:** [DEC-13](../decisions/DEC-13-arquitectura-iot.md)
- **Fecha:** 2026-11-12 (actualizado el 2026-11-13, IoT-04.2)

## 1. Propósito

Definir qué información debe llevar cada trama LoRa del coordinador al gateway,
cómo se firma y qué reglas debe cumplir, sin fijar todavía bytes, pines ni
parámetros de radio no confirmados.

## 2. Especificación técnica

### 2.1 Enlace

- Radio: módulos SX1278, banda de 433 MHz (aprobado).
- Topología: punto a punto, un coordinador hacia un gateway. LoRa simple, **sin
  LoRaWAN**.
- Parámetros de radio (factor de dispersión, ancho de banda, tasa de
  codificación, potencia, frecuencia exacta dentro de la banda) y su
  cumplimiento normativo en Colombia: **pendientes** (P-IOT-05).
- Tamaño máximo útil de la trama: depende de esos parámetros y del módulo. Se
  medirá en hardware (P-IOT-06). El diseño debe asumir que el espacio es
  escaso.

### 2.2 Campos lógicos de la trama (versión 1)

| Campo | Firmado | Propósito |
|---|---|---|
| `frame_version` | Sí | Permite cambiar el formato sin romper el backend. |
| `coordinator_uid` | Sí | Identifica al coordinador y, con él, la clave HMAC a usar. |
| `key_version` | Sí | Permite rotar claves: indica con qué clave se firmó. |
| `boot_counter` | Sí | Número de arranque, persistente, sube en cada reinicio. |
| `seq` | Sí | Contador de tramas dentro del arranque; empieza en 0 tras cada reinicio. |
| `readings[]` | Sí | Lista de (`channel`, `metric_code`, `value`); formato v1 de 5 bytes por lectura en §2.7. |
| `tag` | — | Etiqueta HMAC-SHA256 calculada sobre todos los campos anteriores. |

Campos excluidos del contrato v1 (IoT-04.1, §2.7): `uptime` (antes candidato,
P-IOT-09) y `grupo` (grupo de sensores, antes candidato, P-IOT-04). Ninguno
forma parte de la cabecera ni de los bloques de lectura de v1; incorporarlos
exigiría una decisión formal de evolución del protocolo (nueva
`frame_version`).

Codificación binaria, tamaño de cada campo y formato de los valores (enteros
escalados ×100): fijados en §2.7 (IoT-04.1). El tamaño máximo de la trama sigue
provisional y pendiente de validación física (P-06, P-IOT-06). Longitud del
`tag`: resuelta en IoT-03.2-R3 (P-IOT-12), 32 bytes.

### 2.3 Firma

```
tag = HMAC-SHA256(clave_del_coordinador, CONTEXTO || bytes_de_la_trama_sin_tag)
```

- `||` significa concatenar.
- `CONTEXTO` es una constante fija de dominio. Evita que una firma válida para
  otro uso sirva aquí. Valor aprobado: los bytes ASCII de
  `agrotech:lora-frame:v1` (D-10, §2.7).
- Se firman **los bytes exactos** de la trama. El gateway los reenvía sin
  modificar, y el backend verifica sobre esos mismos bytes. Así se evitan
  diferencias de formato entre el firmware en C y Python.
- Comparación en el backend con tiempo constante (`hmac.compare_digest`).
- **Tag de 32 bytes, sin truncar (P-IOT-12, resuelto en IoT-03.2-R3).** Se
  usa el HMAC-SHA256 completo. El backend fija la longitud y rechaza cualquier
  otra: aceptar la longitud que trae la trama permitiría a un atacante enviar un
  tag de 1 byte y acertarlo con probabilidad 1/256. El coste son 32 bytes de
  radio por trama, a tener en cuenta en el tamaño máximo (P-IOT-06).

### 2.4 Contadores y reinicios

- `boot_counter` se guarda en memoria no volátil del ESP32 (NVS: zona de la
  flash que conserva datos sin energía) y se incrementa **al arrancar, antes de
  la primera transmisión**. Una escritura por arranque, no por trama.
- `seq` vive en RAM y se reinicia en cada arranque.
- La pareja (`boot_counter`, `seq`) nunca debe repetirse para un mismo
  coordinador con contenido diferente. Si se borra la NVS (por ejemplo, al
  reflashear), el coordinador debe **reprovisionarse** con clave nueva o el
  contador debe restaurarse a un valor mayor (procedimiento pendiente,
  P-IOT-13).
- Ventaja frente a un identificador de arranque aleatorio: el contador permite
  ordenar los arranques y detectar con facilidad tramas de un arranque antiguo.

### 2.5 Reglas del receptor (gateway)

- Recibe la trama completa; si no puede leerla íntegra, la descarta.
- **No la modifica.** La codifica en Base64 dentro del mensaje MQTT
  (ver [contrato MQTT](iot-contrato-mqtt.md)). Base64 convierte bytes en texto
  seguro para JSON.
- Agrega fuera de la trama: `lora_rx_at`, `clock_synced`, RSSI y SNR (potencia
  y calidad de la señal recibida, si la biblioteca del SX1278 los entrega).
- No verifica la firma en la primera versión (P-IOT-07).

### 2.6 Confirmación LoRa (opcional)

Un ACK LoRa (mensaje corto del gateway al coordinador que dice "recibí la trama
N") permitiría que el coordinador reintente si no lo recibe. Implica que el
coordinador escuche tras transmitir y que el gateway transmita. Como la
deduplicación del backend tolera reintentos con el mismo `seq`, el ACK puede
añadirse después sin cambiar el backend. Decisión pendiente (P-IOT-08). Si se
adopta, el ACK debería enviarse **después** de guardar la trama en el búfer del
gateway, y su autenticación también debe decidirse.

### 2.7 Contrato binario v1 (IoT-04.1, en curso; no cerrado)

**Estado de cada elemento:** **[Aprobado]**, **[Provisional]** (puede cambiar al
resolver un pendiente), **[Pendiente de decisión]** y **[Pendiente de validación
física]** (requiere pruebas con el hardware real). IoT-04.1 **no está cerrada**:
faltan el rango de temperatura, el tratamiento de valores centinela, el tamaño
MQTT y la variante de Base64, y toda la validación física.

#### Reglas generales [Aprobado]

- Todos los campos numéricos se codifican en **big-endian**.
- `coordinator_uid`: exactamente **13 bytes ASCII** (`CO-` + 10 caracteres de
  `ABCDEFGHJKMNPQRSTUVWXYZ23456789`, solo mayúsculas), **sin terminador nulo**. Se
  valida en forma canónica estricta (sin normalizar): bytes no ASCII, minúsculas
  o caracteres fuera del alfabeto hacen rechazar la trama. Un uid desconocido se
  rechaza sin revelar el motivo.
- Excluidos de v1: `uptime` (P-IOT-09) y `grupo` (P-IOT-04).

#### Cabecera [Aprobado; el máximo de `reading_count` es provisional]

| # | Campo | Offset | Bytes | Tipo | Rango | Estado |
|---|---|---|---|---|---|---|
| 1 | `frame_version` | 0 | 1 | uint8 | 1 | Aprobado |
| 2 | `coordinator_uid` | 1 | 13 | ASCII | `CO-` + 10 | Aprobado |
| 3 | `key_version` | 14 | 2 | uint16 BE | 1–65535 | Aprobado |
| 4 | `boot_counter` | 16 | 4 | uint32 BE | 0–4 294 967 295 | Aprobado |
| 5 | `seq` | 20 | 4 | uint32 BE | 0–4 294 967 295 | Aprobado |
| 6 | `reading_count` | 24 | 1 | uint8 | 1–16 (máximo provisional) | Campo aprobado; máximo provisional (P-06) |

Cabecera: 25 bytes (offsets 0–24). A continuación van `reading_count` bloques
de lectura de 5 bytes, desde el offset 25, y al final la etiqueta HMAC de 32
bytes, en el offset `25 + 5 × reading_count`.

#### `frame_version` (P-08) [Aprobado]

uint8; versión inicial 1. Cualquier otra versión se rechaza. Cada versión tiene
un parser explícito propio, sin compatibilidad implícita.

#### `key_version` (P-01) [Aprobado]

- uint16 big-endian, rango 1–65535. Representa la **generación de la credencial
  del coordinador**: la primera es 1 y cada rotación o reemisión la incrementa.
  Nunca se reutiliza y no se reinicia al reiniciar la ventana anti-replay.
- **No es `master_key_version`**, que sigue identificando la versión de la clave
  maestra AES-GCM con que se cifra la credencial en reposo.
- **Agotamiento:** al llegar a 65535 se detienen las rotaciones y se exige un
  procedimiento administrativo controlado de sustitución o reprovisionamiento.
  El uid **no** cambia automáticamente.
- Hoy `CoordinatorCredential` no tiene campo de generación; incorporarlo es
  trabajo de implementación posterior (no de esta etapa).

#### Contadores (P-07) [Aprobado]

- `boot_counter`: uint32 big-endian, persistente en NVS; aumenta de forma
  persistente en cada nuevo arranque.
- `seq`: uint32 big-endian, en RAM; se reinicia en cada arranque.
- **Sin desbordamiento silencioso.** Si `seq` se agota, el coordinador detiene
  la transmisión y requiere un reinicio controlado; `boot_counter` se incrementa
  y se persiste durante el siguiente arranque físico. Si `boot_counter` se
  agota, el coordinador detiene la transmisión y requiere un reprovisionamiento
  controlado.
- **Nunca** se reinicia automáticamente a cero.

#### Lecturas (P-03, P-04) [Aprobado salvo lo indicado]

| Métrica | Tipo | Escala | Rango válido | Valor inválido reservado | Ejemplo |
|---|---|---|---|---|---|
| `temperature` (°C) | int16 BE | ×100 (0,01 °C) | **Pendiente**: depende del modelo de sensor | −32768 (`0x8000`) | 25,34 °C → 2534 → `09 E6` |
| `relative_humidity` (%) | uint16 BE | ×100 (0,01 %) | 0–10000 | 65535 (`0xFFFF`) | 63,25 % → 6325 → `18 B5` |

La escala ×100 da resolución de 0,01, no precisión: la exactitud depende del
sensor. Representables: int16 de −327,68 a 327,67 °C; uint16 de 0 a 655,35 %.

#### Bloque de lectura (P-05) [Aprobado]

Cada lectura ocupa exactamente **5 bytes**. Offsets relativos al inicio de cada
bloque; el bloque `i` (desde 0) empieza en el offset absoluto `25 + 5 × i`.

| Campo | Offset relativo | Bytes | Tipo |
|---|---|---|---|
| `channel` | 0 | 2 | uint16 BE |
| `metric_code` | 2 | 1 | uint8 |
| `value` | 3 | 2 | int16 BE o uint16 BE según la métrica |

- `metric_code = 1`: `temperature` (`value` int16). `metric_code = 2`:
  `relative_humidity` (`value` uint16).
- `channel` válido: 1–65535; el canal 0 se rechaza (IoT-04.2-4).
- El `metric_code` debe coincidir con la métrica del sensor registrado para ese
  canal; una métrica incompatible se rechaza por el servicio de ingesta Django.
  El parser estructural solo exige un `metric_code` conocido y canales únicos.
- Un canal duplicado dentro de la misma trama se rechaza, aunque las métricas
  sean distintas.
- Antecedente descartado: un bloque de 4 bytes sin `metric_code` (métrica
  derivada del canal) no es el formato vigente.

#### Tamaño (P-06) [Provisional; pendiente de validación física]

- Fórmula: `total_bytes = 25 + (5 × reading_count) + 32`.
- 2 lecturas: 67 bytes; 4 lecturas: 77 bytes; 16 lecturas: 137 bytes.
- Máximo provisional de v1: **16 lecturas (137 bytes)**. **No** es una capacidad
  física validada.
- **255 bytes no es un tamaño operativo garantizado.** El límite de carga útil
  del SX1278, la configuración real de los registros y del controlador, el
  tiempo en aire según SF, BW, CR, preámbulo y CRC, y la normativa colombiana de
  433 MHz (P-IOT-05) siguen pendientes de validación física.

#### Pendientes del contrato v1

- Rango físico válido de temperatura: pendiente del modelo real del sensor y de
  su hoja técnica.
- Tratamiento de una lectura con el valor reservado de inválido (centinela):
  pendiente de decisión.
- Tamaño máximo del mensaje MQTT y variante exacta de Base64: pendientes de
  decisión ([contrato MQTT](iot-contrato-mqtt.md)).

#### Autenticación (P-02) [Aprobado]

- Contexto: bytes ASCII de `agrotech:lora-frame:v1` (22 bytes), **no
  transmitidos**, distinto de `agrotech:co-cred:v1` y `agrotech:gw-cred:v1`.
- `signed_bytes`: todos los bytes de la trama salvo la etiqueta (cabecera y
  lecturas).
- `tag = HMAC-SHA256(clave_del_coordinador, contexto || signed_bytes)`, completo
  de 32 bytes, al final de la trama. Comparación con `hmac.compare_digest`.
- Todos los campos que afectan a autenticación, deduplicación, identificación o
  medición están dentro de `signed_bytes`. No se firma ninguna representación
  JSON alternativa.
- El uid y `key_version` se leen antes de verificar (hacen falta para elegir la
  credencial), pero ningún campo se usa hasta que el HMAC es válido; después,
  todos se toman de esos mismos bytes (requisito M-02).

#### Deduplicación [Aprobado]

Identidad persistente: `(coordinator_id, credential_id, boot_counter, seq)`.
Ver [persistencia §2.3](iot-persistencia-entrega.md). La restricción `UNIQUE`
vive en `ReceivedFrame` y la aplica la persistencia de ingesta Django.

### 2.8 Implementación IoT-04.2: parser v1

Decisiones en [DEC-13 §15](../decisions/DEC-13-arquitectura-iot.md).

**Componente:** `apps/iot/lora_frame.py`, puro (sin Django, ORM, configuración,
red ni archivos).

- **Entrada:** `parse_lora_frame(raw)` recibe la trama completa ya decodificada
  (`signed_bytes` + tag). Acepta `bytes` y `bytearray` (copiado a `bytes`
  inmutables); rechaza cualquier otro tipo, incluidos `str`, `None` y
  `memoryview`. El consumidor MQTT realiza la decodificación Base64 antes de
  llamar al parser.
- **Salida:** `LoraFrameV1` (`frame_version`, `coordinator_uid`, `key_version`,
  `boot_counter`, `seq`, `readings`, `signed_bytes`, `tag`) con una tupla de
  `LoraReading` (`channel`, `metric`, `raw_value` entero escalado ×100). Ambas
  son inmutables (`frozen`, `slots`). El `repr` de `LoraFrameV1` omite
  `coordinator_uid`, `signed_bytes` y `tag`. `to_coordinator_frame()` la adapta
  al `ParsedCoordinatorFrame` de IoT-03.2.
- **Orden de validación:** tipo → vacía → `frame_version == 1` → longitud ≥ 25
  → `reading_count` entre 1 y `MAX_READINGS_V1` (16, provisional) → longitud
  exacta `25 + 5 × reading_count + 32` → corte único de `signed_bytes` y tag →
  cabecera → uid ASCII canónico → `key_version ≥ 1` → por lectura: canal ≥ 1,
  `metric_code` conocido, canal no repetido, valor con signo según la métrica.
- **Errores:** `LoraFrameError` (subclase de `ValueError`) con `reason` de tipo
  `LoraFrameRejection`: `invalid_type`, `empty`, `unsupported_version`,
  `truncated`, `invalid_reading_count`, `length_mismatch`,
  `invalid_coordinator_uid`, `invalid_key_version`, `invalid_channel`,
  `unknown_metric_code` y `duplicate_channel`. El mensaje es fijo ("trama LoRa
  no válida") y ni el mensaje ni el `repr` exponen bytes, tag ni uid.
- **Centinelas:** `LoraReading.is_invalid_marker` identifica −32768
  (temperatura) y 65535 (humedad). No se rechazan ni se aplica una política de
  almacenamiento o descarte (pendiente).
- **Contexto HMAC:** constante `LORA_FRAME_HMAC_CONTEXT`
  (`b"agrotech:lora-frame:v1"`).

**M-02.** `validate_coordinator_frame` (`apps/iot/coordinator_credentials.py`)
vuelve a interpretar `signed_bytes + tag` con `parse_lora_frame` y exige que
`coordinator_uid`, `key_version`, `boot_counter`, `seq`, `signed_bytes` y `tag`
coincidan exactamente; si no, la trama se rechaza antes de consultar la base.
Antes del HMAC solo se usa el uid, para localizar la credencial vigente;
`key_version` no se usa todavía para seleccionarla ni se compara con una
generación persistida (pendiente).

**Estructura frente a autenticación.** Que una trama supere el parser y la
comprobación de coherencia no la autentica: ningún campo es de confianza hasta
que `verify_coordinator_frame` verifica el HMAC sobre esos mismos
`signed_bytes`. Las lecturas no forman parte de `ParsedCoordinatorFrame`; su
integridad la garantiza solo el HMAC.

**Fuera del parser:** Base64, verificación HMAC, rangos físicos, existencia del
sensor, correspondencia entre `metric_code` y la métrica del sensor, pertenencia
a la finca y coincidencia con el gateway, antirreplay, deduplicación y
persistencia.

**Vector de referencia** (`apps/iot/tests/lora_vectors.py`, construido con
`struct.pack` sin usar el parser): trama de 67 bytes con uid `CO-7K3M9QXT2A`,
`key_version` 1, `boot_counter` 7, `seq` 120, canal 1 temperatura 2534
(25,34 °C) y canal 2 humedad 6325 (63,25 %).

```
signed_bytes (35 B): 01 434f2d374b334d395158543241 0001 00000007 00000078 02
                     00010109e6 00020218b5
tag (32 B):          ddbab481e3b4f71dcc93452b1d0943ad24163c59d6a3c2e6f27ad3eb6bab1af5
```

El tag es `HMAC-SHA256(bytes(range(32)), b"agrotech:lora-frame:v1" ||
signed_bytes)`; la clave es solo de prueba.

## 3. Explicación sencilla

Cada paquete lleva: quién lo manda, en qué "encendida" va el aparato (el
contador de arranques), qué número de paquete es en esa encendida, las lecturas,
y al final un sello calculado con una clave que solo conocen el coordinador y
el backend. Si alguien cambia un número, el sello deja de coincidir. Si alguien
graba un paquete y lo repite, el backend ve que ese número ya llegó.

## 4. Ejemplo práctico (ilustrativo, no es el formato definitivo)

```
frame_version   = 1
coordinator_uid = C-A
key_version     = 1
boot_counter    = 7
seq             = 120
readings        = [(1, 27.4), (2, 63.0)]
tag             = HMAC-SHA256(clave C-A v1, CONTEXTO || campos anteriores)
```

Tras un corte de energía, la siguiente trama será `boot_counter = 8, seq = 0`.

El vector binario real del formato v1, con sus bytes y su tag, está en §2.8.

## 5. Decisiones aprobadas

- SX1278 en la banda de 433 MHz.
- HMAC-SHA256 con clave propia por coordinador.
- Primera versión con sensores cableados al coordinador.

## 6. Aspectos pendientes

- P-IOT-04 "Grupo de sensores": excluido de la trama v1 (IoT-04.1).
- P-IOT-05 Parámetros de radio y cumplimiento normativo.
- P-IOT-06 Parcialmente resuelto en IoT-04.1 (§2.7): cabecera y bloque de
  lectura aprobados; pendientes el rango de temperatura, el tratamiento de
  centinelas y la validación física del tamaño máximo (P-06, provisional).
- P-IOT-07 Si el gateway verifica también la firma.
- P-IOT-08 ACK LoRa y su autenticación.
- ~~P-IOT-09 Campo `uptime`~~: excluido de v1 (IoT-04.1).
- ~~P-IOT-12 Longitud del `tag`~~: resuelto (32 bytes, IoT-03.2-R3).
- P-IOT-13 Procedimiento ante borrado de NVS o reemplazo de placa.

## 7. Riesgos y consideraciones

- HMAC autentica, **no cifra**: las lecturas se pueden escuchar (R-IOT-01).
- La radio es compartida y la emisión puede ser bloqueada o interferida; HMAC
  no evita la pérdida de tramas (R-IOT-02).
- Una clave extraída de un coordinador robado permite falsificar sus tramas
  hasta que se revoque (R-IOT-03).
