# IoT-01 — Seguridad y autenticación

- **Etapa:** IoT-01 (solo documentación)
- **Estado de implementación:** Diseñada
- **Decisión asociada:** [DEC-13](../decisions/DEC-13-arquitectura-iot.md)
- **Fecha:** 2026-11-12

## 1. Propósito

Definir cómo se autentica cada componente, cómo se gestionan las claves y cómo
se garantiza que ningún mensaje pueda asignarse por sí mismo a una finca.

## 2. Especificación técnica

### 2.1 Credenciales por componente

| Componente | Credencial | Protege | Quién la verifica |
|---|---|---|---|
| Coordinador | Clave HMAC simétrica propia (≥ 32 bytes aleatorios) con `key_version` | Autenticidad e integridad de cada trama LoRa | Consumidor Django |
| Gateway | Usuario y contraseña MQTT propios (usuario = `gateway_uid`) sobre TLS | Acceso al broker y topic autorizado | Mosquitto |
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
  guardará **cifrada en reposo** con una clave maestra dedicada, leída del
  entorno y validada al arrancar, siguiendo el patrón de
  `config/invitation_code_key.py`. La dependencia `cryptography` ya está en
  `requirements.txt`. Algoritmo y formato exactos: P-IOT-20.
- **Entrega al dispositivo:** se muestra una sola vez y se carga en el
  coordinador en un procedimiento físico controlado. El procedimiento es
  pendiente (P-IOT-21).
- **Rotación:** nueva clave con `key_version` + 1. Durante un periodo de
  transición, el backend acepta la versión anterior; luego la retira (P-IOT-22).
- **Revocación:** al desactivar un coordinador, el backend rechaza todas sus
  tramas, aunque la firma sea válida.
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

- Una trama repetida trae el mismo (`coordinator_uid`, `boot_counter`, `seq`) y
  la deduplicación la descarta: **no produce datos nuevos**.
- Mientras se conserven las claves de deduplicación de un coordinador, no hace
  falta más. Si una política de retención borrara mediciones antiguas, debe
  conservarse por coordinador el último (`boot_counter`, `seq`) aceptado,
  llamado *marca de agua*, y rechazarse cualquier trama de un `boot_counter`
  inferior (P-IOT-26).
- Un atacante **no** puede fabricar un `boot_counter` mayor sin la clave.

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
- P-IOT-20 Algoritmo y formato de cifrado en reposo de las claves HMAC.
- P-IOT-21 Procedimiento de carga de claves en el coordinador.
- P-IOT-22 Periodo de transición en rotaciones.
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
