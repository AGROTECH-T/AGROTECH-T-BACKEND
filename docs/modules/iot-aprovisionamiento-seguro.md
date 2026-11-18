# BUILD-IOT-02 — Aprovisionamiento seguro de dispositivos

## Alta y entrega

Un administrador de finca registra coordinadores y gateways con `POST` a
`/api/v1/iot/farms/{farm_id}/coordinators/` o `/gateways/`, enviando solo
`name` y opcionalmente `location_label`. El servidor genera el UID, crea la
credencial individual y audita la operación dentro de una transacción. El
coordinador recibe una clave aleatoria de 32 bytes cifrada AES-256-GCM en la
base; el gateway recibe una contraseña aleatoria de 256 bits y el servidor
conserva únicamente su huella HMAC. La respuesta de alta contiene el material
de aprovisionamiento una vez y usa `Cache-Control: no-store`. Debe consumirse
por un canal administrativo protegido y cargarse físicamente al dispositivo;
no se debe guardar en frontend, historial del navegador o logs.

## Rotación, revocación e historial

`GET .../{uid}/credentials/` devuelve solo versión, fechas, causa y estado.
`POST` al mismo recurso con `{"action":"rotate"}` revoca la credencial activa
y devuelve la nueva una sola vez. Con `{"action":"revoke"}` se revoca sin
emitir otra. Solo administradores de la finca pueden escribir. Las credenciales
revocadas no vuelven a ser aceptadas. Al desactivar el dispositivo también se
revoca su credencial vigente.

## Validación

La ingesta valida la pareja autorizada gateway/coordinador y la trama Frame v1
contra la clave vigente, su versión y la ventana anti-replay. La finca siempre
se resuelve desde el registro. El firmware se conecta al broker con username
individual (UID) y contraseña individual por TLS, y solo publica en el topic
del propio UID. La verificación de la contraseña y el ACL deben configurarse en
el broker con el plugin o almacén de credenciales correspondiente; este
repositorio no contiene una configuración de broker productiva ni parámetros
de producción. No se habilitó ninguna publicación real.

## Límites del dispositivo

El coordinador almacena su clave en NVS del ESP32 mediante carga USB serial
local. NVS no equivale a un elemento seguro dedicado; sin cifrado NVS/flash
aprovisionado en eFuses, un atacante con acceso físico o lectura de flash puede
recuperar el material. La clave no se imprime ni se compila en el firmware.
La política de eFuses y la carga física deben validarse por el propietario en
una instalación controlada. El gateway requiere transporte MQTT TLS y CA
configurada antes de conectarse a un broker real.
