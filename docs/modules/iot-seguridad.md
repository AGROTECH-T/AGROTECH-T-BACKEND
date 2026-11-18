# IoT-01 — Seguridad y autenticación

- **Etapa:** IoT-01 (solo documentación)
- **Estado de implementación:** Diseñada
- **Decisión asociada:** [DEC-13](../decisions/DEC-13-arquitectura-iot.md)
- **Fecha:** 2026-11-12

## 1. Propósito

Definir cómo se autentica cada componente, cómo se gestionan las claves y cómo
se garantiza que ningún mensaje pueda asignarse por sí mismo a una finca.

### Arquitectura vigente de ingesta

Django valida y procesa la ingesta; `apps/iot/services/ingestion.py` es el
único responsable de escribirla en MariaDB. El consumidor MQTT delega en ese
servicio. No hay Writer independiente ni autenticación mTLS Django → Writer.
El principal `django-iot-ingestion` se fija dentro del servicio Django tras
validar gateway, coordinador y HMAC; nunca se toma del payload. UUIDv5 y SHA-256
son identificadores/huellas, no autenticadores ni controles de autorización.
La autenticación de gateway en Mosquitto y la verificación HMAC de la trama en
Django siguen siendo controles distintos y vigentes.

## 2. Especificación técnica

### 2.1 Credenciales por componente

| Componente | Credencial | Protege | Quién la verifica |
|---|---|---|---|
| Coordinador | Clave HMAC simétrica propia (≥ 32 bytes aleatorios) con `key_version` de trama y `master_key_version` de cifrado en reposo | Autenticidad e integridad de cada trama LoRa | Consumidor Django |
| Gateway | Usuario (`gateway_uid`) y secreto propio `gws_…` sobre TLS; el backend guarda solo su huella HMAC (IoT-03.1, [ciclo de vida](iot-credenciales-gateway.md)) | Acceso al broker y topic autorizado | Mosquitto (integración pendiente); el backend ya verifica con `verify_gateway_credential` |
| Consumidor | Usuario MQTT propio, solo suscripción | Lectura de los topics `up` y `status` | Mosquitto |
| Usuario humano | Sesión JWE o cookie existente (`apps/accounts`) | API REST | Django |

**TLS:** cifrado del canal TCP (el mismo de HTTPS). **Clave simétrica:** la
misma clave sirve para firmar y para verificar; por eso el backend necesita
conocerla.

### 2.2 Claves HMAC de coordinador

- **Generación:** en el backend, con un generador criptográfico, al registrar el
  coordinador. Una clave distinta por coordinador.
- **Almacenamiento en el backend:** a diferencia de los códigos de invitación
  (`apps/farms/invitation_codes.py`, que guardan solo una huella), **aquí no
  basta un hash**: para verificar una HMAC se necesita la clave original. Se
  guarda **cifrada en reposo** con AES-256-GCM y una clave maestra dedicada
  (`COORDINATOR_KEY_ENCRYPTION_KEY`, Base64 de 32 bytes, versionada), leída del
  entorno y validada al arrancar en producción. Implementado en IoT-03.2 (ver
  [ciclo de vida](iot-credenciales-coordinador.md)); DEC-13 §13.
- **Entrega al dispositivo:** se muestra una sola vez y se carga en el
  coordinador en un procedimiento físico controlado. El procedimiento es
  pendiente (P-IOT-21).
- **Rotación inmediata:** la credencial nueva se emite con su propia versión y la
  anterior se revoca en la misma transacción; **no hay periodo de coexistencia**.
  Reemitir tras una pérdida usa la causa `reissued` y reinicia la ventana
  antirreplay del coordinador en esa misma transacción, para que un equipo
  reemplazado pueda arrancar de nuevo en 0. La rotación ordinaria no la reinicia.
  La rotación de la clave maestra sí conserva versiones históricas para
  descifrar credenciales antiguas.
- **Versión persistida:** `master_key_version` debe estar entre 1 y 65535 (límite
  de `SMALLINT UNSIGNED`); valores fuera de rango se rechazan antes de escribir.
  No se confunde con la `key_version` que declara la trama LoRa.
- **Revocación:** al desactivar un coordinador, el backend revoca sus credenciales
  vigentes y rechaza todas sus tramas, aunque la firma sea válida. Reactivar no
  las restaura. La base exige causa y fecha en toda credencial revocada;
  `revoked_by` puede quedar `NULL` si se borra la cuenta, sin perder el historial.
- **Auditoría:** alta, rotación y revocación como `AuditEvent`
  (`apps/trazabilidad`), **nunca con la clave** en `before_data`, `after_data` ni
  `metadata`. `record_audit_event` ya rechaza claves sensibles en los datos.
- **Logs:** prohibido registrar claves, etiquetas HMAC o contraseñas.
- **Protección en el ESP32:** las funciones de cifrado de la flash y de
  arranque seguro del ESP32 dificultan extraer la clave de una placa robada. Su
  adopción queda pendiente (P-IOT-23) por su impacto en el flujo de grabación.

### 2.3 Gateway y broker

- Conexión **TLS** obligatoria; acceso anónimo desactivado.
- **ACL** (lista de control de acceso del broker):
  - Gateway `G`: solo puede **publicar** en `agrotech/v1/gw/G/up` y
    `agrotech/v1/gw/G/status`. No puede suscribirse a topics de otros.
  - Consumidor: solo puede **suscribirse** a `agrotech/v1/gw/+/up` y
    `agrotech/v1/gw/+/status`.
- Mecanismo de Mosquitto (archivo de contraseñas, plugin de seguridad dinámica
  u otro) y gestión de certificados en el ESP32: pendientes (P-IOT-24). No se
  crea configuración operativa en IoT-01.
- La contraseña del gateway la necesita Mosquitto, no Django. Si el backend
  participa en el aprovisionamiento, guardará como máximo una huella, no la
  contraseña.
- **IoT-03.1 (implementado):** el backend emite, rota y revoca la credencial de
  cada gateway y guarda solo `HMAC-SHA256(GATEWAY_CREDENTIAL_HMAC_KEY, ...)`
  (ver [ciclo de vida](iot-credenciales-gateway.md)). Esa huella **no equivale**
  al hash de contraseña que usa Mosquitto: la integración futura deberá
  resolver cómo aprovisionar el broker y propagar las revocaciones sin persistir
  secretos en texto claro. Decisión aprobada: las credenciales MQTT las
  administrará Mosquitto; el mecanismo concreto sigue pendiente (P-IOT-24).

### 2.4 Aislamiento por finca: el mensaje no decide la finca

La finca **nunca** se lee del mensaje. Se deriva así:

```
credencial MQTT del gateway ──(Mosquitto + ACL)──▶ topic agrotech/v1/gw/G/up
                                                     │
                       gateway_uid del topic ──▶ Gateway registrado ──▶ farm_id
                                                     │
                 coordinator_uid de la trama ──▶ Coordinador registrado
                                                     │
                     ¿coordinador.farm == gateway.farm?  no ──▶ rechazo
                     ¿HMAC válida con clave del coordinador? no ──▶ rechazo
                                                     │
                                       mediciones guardadas con farm_id
```

Defensa en profundidad:

1. **Broker:** un gateway solo publica en su propio topic.
2. **Backend:** el topic identifica un gateway registrado y activo en una finca
   activa.
3. **Backend:** el coordinador debe pertenecer a **la misma finca** que el
   gateway. Así, aunque un gateway de la finca A capte por radio una trama
   auténtica de un coordinador de la finca B, esa trama se rechaza.
4. **Firma:** solo quien tiene la clave del coordinador puede producir tramas
   válidas.
5. **Consultas REST:** filtradas por `farm_id` con membresía activa, igual que
   inventario.

Si una finca se desactiva, sus mensajes se rechazan como permanentes
(confirmados y no guardados), coherente con DEC-01.

Pendiente: si un coordinador queda ligado a un único gateway o a cualquier
gateway de su finca (P-IOT-25). La recomendación es "cualquier gateway de su
finca": permite redundancia sin abrir acceso entre fincas.

### 2.5 Protección frente a repeticiones (*replay*)

**Replay:** un atacante graba una trama válida y la retransmite más tarde.

- Una trama repetida trae la misma identidad (`coordinator_id`,
  `credential_id`, `boot_counter`, `seq`) (D-01, IoT-04.1) y la deduplicación
  la descarta: **no produce datos nuevos**. Si llega con la misma identidad y
  bytes distintos, es un conflicto que se registra (IoT-04.3-9).
- Mientras se conserven las claves de deduplicación de un coordinador, no hace
  falta más. Si una política de retención borrara mediciones antiguas, debe
  conservarse por coordinador el último (`boot_counter`, `seq`) aceptado,
  llamado *marca de agua*, y rechazarse cualquier trama de un `boot_counter`
  inferior (P-IOT-26).
- Un atacante **no** puede fabricar un `boot_counter` mayor sin la clave.
- Antes de tocar la ventana, `accept_coordinator_sequence` revalida
  `coordinator_id`, `coordinator_uid` y `farm_id` contra el coordinador
  persistido y exige coordinador y finca activos. Un frame manipulado o de otra
  finca se rechaza sin crear ni modificar la ventana.
- Una reemisión reinicia la ventana en la misma transacción para permitir de
  nuevo `boot_counter` 0; una rotación ordinaria la conserva. La credencial
  anterior ya está revocada, así que no puede autenticar.
- Cuando falta la credencial o el ciphertext no autentica, el backend calcula un
  HMAC descartable para que el tiempo de rechazo no distinga ambos casos.

## 3. Explicación sencilla

Hay dos cerraduras. La del broker: cada gateway tiene su llave y solo puede
dejar mensajes en su propio buzón. La del coordinador: cada paquete viene
sellado con una clave secreta que solo conocen ese coordinador y el backend. El
backend averigua la finca mirando de qué buzón vino el mensaje, y además exige
que el coordinador que lo selló sea de esa misma finca. El mensaje puede decir
lo que quiera: nunca se le cree sobre la finca.

## 4. Ejemplo práctico

Intento de inyectar datos en otra finca:

1. Alguien conecta un gateway propio con credenciales de la finca A y publica
   un mensaje cuyo cuerpo dice `"farm_id": 99`.
2. El consumidor rechaza el mensaje por campo no permitido.
3. Si omite el campo e intenta usar un `coordinator_uid` de la finca 99, el
   backend rechaza porque ese coordinador no pertenece a la finca A, y además no
   puede firmar sin su clave.

## 5. Decisiones aprobadas

- HMAC-SHA256 por coordinador para autenticar tramas LoRa.
- Mosquitto como broker; React sin acceso MQTT.
- La finca se deriva del gateway registrado, no del contenido del mensaje.

## 6. Aspectos pendientes

- P-IOT-07 Verificación de firma también en el gateway.
- P-IOT-20 Algoritmo y formato de cifrado en reposo de las claves HMAC
  (resuelto en IoT-03.2: AES-256-GCM; P-IOT-12 resuelto en IoT-03.2-R3 con un
  tag HMAC-SHA256 completo de 32 bytes; P-IOT-06 sigue abierto).
- P-IOT-21 Procedimiento de carga de claves en el coordinador.
- P-IOT-22 Periodo de transición en rotaciones (resuelto en IoT-03.2: rotación
  inmediata, sin coexistencia).
- P-IOT-23 Cifrado de flash y arranque seguro del ESP32.
- P-IOT-24 Mecanismo de autenticación de Mosquitto y certificados TLS.
- P-IOT-25 Coordinador ligado a un gateway o a la finca.
- P-IOT-26 Marca de agua si existe retención.

## 7. Riesgos y consideraciones

- Lecturas no cifradas en radio (R-IOT-01).
- Robo de un coordinador expone su clave hasta revocarla (R-IOT-03).
- Pérdida de la clave maestra impide verificar todas las tramas; su
  respaldo es parte de la operación (R-IOT-09).
- Un error en la ACL anularía la primera barrera de aislamiento; las barreras
  2 a 4 del backend siguen activas, pero la ACL debe probarse (R-IOT-10).
