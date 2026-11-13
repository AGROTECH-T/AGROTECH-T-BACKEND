# IoT-01 — Contrato de comunicación LoRa

- **Etapa:** IoT-01 (solo documentación)
- **Estado de implementación:** Diseñada a nivel lógico. **La estructura binaria
  definitiva de la trama está pendiente** y se fijará con pruebas sobre el
  hardware real.
- **Decisión asociada:** [DEC-13](../decisions/DEC-13-arquitectura-iot.md)
- **Fecha:** 2026-11-12

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
| `readings[]` | Sí | Lista de (`canal`, `valor`). |
| `tag` | — | Etiqueta HMAC-SHA256 calculada sobre todos los campos anteriores. |

Campos candidatos sin decidir: `uptime` (P-IOT-09) y `grupo` (P-IOT-04).

Codificación (binaria compacta frente a texto), tamaño de cada campo, formato
de los valores (por ejemplo, enteros escalados) y longitud del `tag`: pendientes
(P-IOT-06, P-IOT-12).

### 2.3 Firma

```
tag = HMAC-SHA256(clave_del_coordinador, CONTEXTO || bytes_de_la_trama_sin_tag)
```

- `||` significa concatenar.
- `CONTEXTO` es una constante fija de dominio (por ejemplo, el texto que
  identifique "trama LoRa AGROTECH v1"). Evita que una firma válida para otro
  uso sirva aquí. Su valor exacto se fija con la estructura binaria.
- Se firman **los bytes exactos** de la trama. El gateway los reenvía sin
  modificar, y el backend verifica sobre esos mismos bytes. Así se evitan
  diferencias de formato entre el firmware en C y Python.
- Comparación en el backend con tiempo constante (`hmac.compare_digest`).
- Una etiqueta truncada (por ejemplo, 16 de 32 bytes) ahorra espacio de radio
  y sigue siendo segura para este uso; la longitud final es P-IOT-12.

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

## 5. Decisiones aprobadas

- SX1278 en la banda de 433 MHz.
- HMAC-SHA256 con clave propia por coordinador.
- Primera versión con sensores cableados al coordinador.

## 6. Aspectos pendientes

- P-IOT-04 "Grupo de sensores": significado y si viaja en la trama.
- P-IOT-05 Parámetros de radio y cumplimiento normativo.
- P-IOT-06 Estructura binaria, tamaño máximo y codificación de valores.
- P-IOT-07 Si el gateway verifica también la firma.
- P-IOT-08 ACK LoRa y su autenticación.
- P-IOT-09 Campo `uptime`.
- P-IOT-12 Longitud del `tag`.
- P-IOT-13 Procedimiento ante borrado de NVS o reemplazo de placa.

## 7. Riesgos y consideraciones

- HMAC autentica, **no cifra**: las lecturas se pueden escuchar (R-IOT-01).
- La radio es compartida y la emisión puede ser bloqueada o interferida; HMAC
  no evita la pérdida de tramas (R-IOT-02).
- Una clave extraída de un coordinador robado permite falsificar sus tramas
  hasta que se revoque (R-IOT-03).
