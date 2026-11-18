# Despliegue IoT en Render: API, MQTT y worker

Estado: guía de preparación. No acredita una conexión con Render, Layerbase ni Flespi.

## Arquitectura de procesos

Construir una imagen desde el stage `production` del `Dockerfile`. Ejecutar tres servicios independientes que compartan imagen y las variables comunes:

| Servicio | Tipo en Render | Comando |
|---|---|---|
| API | Web Service Docker | El `CMD` de la imagen inicia Gunicorn en `0.0.0.0:${PORT:-8080}`. |
| Consumidor | Background Worker Docker | `python manage.py mqtt_consumer` |
| Worker durable | Background Worker Docker | `python manage.py mqtt_delivery_worker --poll-seconds 1 --batch-size 10` |

La API responde en `GET /api/health/` con estado HTTP 200 y JSON `status=ok`. Este endpoint solo comprueba que Django responde; no comprueba MariaDB, Redis, MQTT ni el worker.

La imagen no debe ejecutar migraciones al arrancar. Para un cambio de esquema, ejecutar una sola vez y de forma controlada `python manage.py migrate` usando la misma imagen y configuración de base de datos. Confirmar antes la compatibilidad y los permisos con Layerbase. Los Background Workers solo ejecutan su comando de proceso.

El consumidor usa MQTT 5, QoS 1, el filtro fijo `agrotech/v1/gw/+/up`, TLS obligatorio en `DEBUG=False`, verificación de certificado y hostname cuando se configura una CA, sesión persistente (`clean_start=False` con `SessionExpiryInterval` explícito, `MQTT_SESSION_EXPIRY_SECONDS`, 86 400 s por defecto) y ACK manual (PUBACK) solo después de la persistencia durable. Esta configuración se validó localmente con Mosquitto 2.1.2 (BUILD-FLEPSI-03); con Flespi está pendiente de validar. Usar una sola instancia con un `MQTT_CLIENT_ID` estable y exclusivo.

El worker consulta entregas pendientes continuamente, usa lease y fencing, respeta FIFO por carril, procesa en lotes y registra los errores en stdout. Ambos procesos manejan SIGTERM de forma ordenada. Configurar el tiempo de apagado de la plataforma para superar el tiempo máximo del trabajo en curso; el lease IoT es de 60 segundos.

## Variables de entorno

No incluir valores en el repositorio. Render debe inyectar los valores desde su almacén de secretos o desde servicios existentes.

### Compartidas por los tres procesos

| Variable | Uso |
|---|---|
| `DJANGO_DEBUG` | Debe ser `false` en producción. |
| `DJANGO_SECRET_KEY` | Clave de Django, obligatoria en producción. |
| `DJANGO_ALLOWED_HOSTS` | Hosts autorizados por Django; si falta, el código consulta `ALLOWED_HOSTS`. |
| `DB_ENGINE` | Backend; la integración IoT requiere MariaDB. |
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` | Conexión a Layerbase. |
| `DB_SSL_MODE` | TLS MariaDB/PyMySQL: `required` exige TLS sin validar certificado; `verify_identity` valida cadena y hostname. |
| `DB_SSL_CA` | Ruta a CA PEM cuando se usa `verify_identity`; si falta, se intenta el almacén de CA del sistema. |
| `DB_SSL` | Compatibilidad heredada. El valor `true` sin `DB_SSL_MODE` solo habilita TLS oportunista y permite fallback en claro; no usar para producción. |
| `REDIS_URL` | Obligatoria con `DEBUG=false`; configura la caché compartida. |
| `COORDINATOR_KEY_ENCRYPTION_KEY` | Clave maestra para credenciales de coordinador y payload MQTT cifrado. |
| `COORDINATOR_KEY_ENCRYPTION_KEY_VERSION` | Versión activa de la clave maestra. |
| `COORDINATOR_KEY_ENCRYPTION_KEY_PREVIOUS` | Versiones anteriores durante rotación, si aplica. |
| `GATEWAY_CREDENTIAL_HMAC_KEY` | HMAC de credenciales de gateway. |
| `INVITATION_CODE_HMAC_KEY` | HMAC de códigos de invitación. |
| `LOG_LEVEL` | Nivel de los logs JSON enviados a stdout. |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` | Envío de correo del backend. No se necesitan para el transporte MQTT, pero las funciones de cuentas que envían correo dependen de su configuración. |
| `FRONTEND_URL` | URL de frontend usada por los flujos de cuenta y correo. |

Todas las claves criptográficas deben ser únicas, persistentes, de alta entropía y distintas de `DJANGO_SECRET_KEY`. No rotar ni regenerar la clave maestra mientras haya credenciales o entregas cifradas con su versión sin planificar la migración de claves.

### Solo API

| Variable | Uso |
|---|---|
| `PORT` | Puerto HTTP asignado por Render; Gunicorn usa 8080 como fallback local. |
| `WEB_CONCURRENCY` | Número de procesos Gunicorn; su valor debe ajustarse a memoria y plan. |
| `CORS_ALLOWED_ORIGINS` | Orígenes exactos del frontend. La API exige JWE Bearer o sesión autenticada. |

### Solo consumidor MQTT

| Variable | Uso |
|---|---|
| `MQTT_HOST`, `MQTT_PORT` | Host y puerto TLS del broker. El puerto por defecto del código es 1883; configurar el puerto TLS real de Flespi. |
| `MQTT_CLIENT_ID` | Identificador fijo y exclusivo del consumidor. |
| `MQTT_USERNAME`, `MQTT_PASSWORD` | Credenciales del broker; la contraseña, si se da, requiere usuario. Confirmar el esquema de autenticación admitido por Flespi. |
| `MQTT_TLS` | Debe ser `true` en producción. |
| `MQTT_CA_CERTS` | CA PEM opcional; si se omite, se usan las CAs del sistema. |
| `MQTT_TLS_INSECURE` | Debe ser `false`/no definido en producción. |
| `MQTT_KEEPALIVE` | Keepalive MQTT; por defecto 60 s. |
| `MQTT_RECONNECT_MIN_DELAY`, `MQTT_RECONNECT_MAX_DELAY` | Espera de reconexión; por defecto 1 y 60 s. |
| `MQTT_MAX_PERSISTENCE_FAILURES`, `MQTT_MAX_CONNECT_FAILURES` | Umbrales para terminar con error y permitir reinicio supervisado. |

### Solo worker durable

| Variable | Uso |
|---|---|
| `IOT_MQTT_DELIVERY_MAX_ATTEMPTS` | Máximo de intentos antes de `DEAD_LETTER`; por defecto 8. |
| `IOT_MQTT_DELIVERY_RETRY_BASE_SECONDS` | Espera inicial del backoff; por defecto 5 s. |
| `IOT_MQTT_DELIVERY_RETRY_MAX_SECONDS` | Tope del backoff; por defecto 900 s. |

También se pueden fijar los límites en el comando con `--max-attempts`, `--retry-base-seconds` y `--retry-max-seconds`.

### Frontend React

`VITE_API_URL` se consume durante el build del frontend y debe apuntar al backend público. `CORS_ALLOWED_ORIGINS` en la API debe contener el origen exacto publicado por Render.

## Bloqueos que debe resolver el responsable

### Layerbase

- `config/database.py` usa PyMySQL vía `pymysql.install_as_MySQLdb()`; no usa el paquete nativo mysqlclient del entorno local.
- Con `DB_SSL_MODE=required`, PyMySQL marca TLS como obligatorio y falla si el servidor no lo ofrece, pero no verifica la identidad del servidor. Preferir `verify_identity` y confirmar que la CA del proveedor o el almacén de CA de la imagen valida el certificado y hostname de Layerbase.
- Probar el cifrado de la sesión desde una instancia de aplicación con `SHOW STATUS LIKE 'Ssl_cipher'`; comprobar que el resultado no está vacío. Confirmar con Layerbase la versión MariaDB, el nombre DNS TLS y los permisos del usuario.
- Las migraciones IoT 0012 y 0013 instalan triggers mediante `RunPython` no atómico. El instalador exige MariaDB 11.8 y compara las definiciones existentes. Layerbase debe permitir el DDL de las migraciones, incluido `CREATE TRIGGER`; verificar con el proveedor cualquier restricción de triggers, binlog y permisos. No ejecutar migraciones como parte de la prueba de conectividad.
- La autenticación de ingesta falla si Django no identifica la conexión como MariaDB. No asumir compatibilidad por el nombre comercial del servicio.

### Redis

`config/platform.py` rechaza el arranque en producción si falta `REDIS_URL`. Configura en los tres servicios la URL privada del Redis contratado. Las vistas de cuenta y los límites de intentos usan la caché; además, todos los procesos cargan la configuración común de Django y requieren la variable aunque el comando no use caché en cada operación.

No se inspeccionó la cuenta Render. El estado, URL privada, TLS, capacidad, región y disponibilidad de Redis siguen pendientes de confirmar en el panel o con el responsable.

### Flespi

El código no contiene una integración específica de Flespi: usa el cliente MQTT estándar y un topic fijo. Confirmar host, puerto TLS, credenciales, ACL de lectura para `agrotech/v1/gw/+/up`, sesión persistente y políticas de retención con Flespi. Pendiente de validar con Flespi: que acepte MQTT 5 para el suscriptor, la sesión persistente con `clean_start=False` y el `SessionExpiryInterval` solicitado (y sus posibles límites), QoS 1 y el PUBACK manual. No desplegar varias instancias con el mismo client ID.

El backend deriva el gateway candidato del topic. La confianza de identidad del coordinador proviene de la firma Frame v1 y de la autorización de ingesta. El ACL del broker debe limitar a cada publicador al topic de su gateway; una credencial de consumidor no autentica a los publicadores.

## Secuencia de despliegue controlado

1. Confirmar en Render la disponibilidad de Redis y registrar su URL privada en los tres servicios.
2. Confirmar con Layerbase MariaDB 11.8, TLS verificable, conectividad privada y permisos DDL requeridos. Validar primero en una base de staging autorizada por el responsable.
3. Preparar los secretos compartidos y validar que son diferentes entre sí. Configurar `DB_SSL_MODE=verify_identity` y la CA si corresponde; validar `SHOW STATUS ... Ssl_cipher`.
4. Construir la imagen y probar el arranque de la API y `GET /api/health/`. La API no ejecuta migraciones al iniciar.
5. Ejecutar `python manage.py migrate` una sola vez en el procedimiento controlado del despliegue; revisar el resultado y el estado de las migraciones antes de activar los workers.
6. Crear/confirmar finca, gateway, coordinador, fuentes y sensores mediante la API autorizada. Conservar las claves emitidas de forma segura; no ponerlas en logs.
7. Configurar y verificar en Flespi el usuario consumidor, TLS, ACL, topic, QoS 1 y sesión persistente. Ejecutar `python manage.py mqtt_consumer --check` como comprobación de configuración; no publica ni conecta.
8. Activar una instancia del Background Worker MQTT y una del worker durable. Enviar un frame de prueba válido y confirmar: fila `RECEIVED`, ACK posterior, transición a `ACCEPTED` y mediciones persistidas.
9. Iniciar el frontend con `VITE_API_URL`, comprobar CORS, autenticación y `GET /api/v1/iot/farms/<farm_id>/readings/` desde la interfaz.
10. Comprobar logs, reinicio ordenado y recuperación tras reiniciar cada Background Worker. Configurar alertas externas de proceso caído; la aplicación no publica un health check MQTT/worker.

## Manifiesto Render

No se añade `render.yaml`: esta inspección no dispone de los nombres/identificadores de servicios existentes, región, planes ni referencia real del Redis contratado. Esos datos son necesarios para describir fielmente el blueprint y no deben inventarse. La tabla de procesos y comandos anterior sirve como configuración manual revisable en el panel.

No ejecutar el `CMD` histórico de una imagen ya desplegada como prueba: el `Dockerfile` actual separa Gunicorn de la migración, pero la imagen desplegada solo cambiará al construir y publicar una nueva imagen.

## Pruebas y límites de validación local

En Windows, las pruebas sin base se ejecutan con `PYTHON_DOTENV_DISABLED=1` para impedir que `config/__init__.py` cargue un `.env` local. La suite MQTT durable requiere el orquestador IoT y su MariaDB desechable. La prueba E2E con broker solo corre si existe el descriptor local temporal; no debe sustituirse por Flespi o Layerbase.

Pendientes externos: verificación de TLS y permisos en Layerbase; disponibilidad de Redis en Render; conexión, ACL y persistencia de sesión en Flespi; despliegue real y lectura desde React. Ninguno se considera validado por esta guía.

## Identidad del broker (BUILD-FLEPSI-02)

El consumer usa MQTT 5 (`clean_start=False`, `MQTT_SESSION_EXPIRY_SECONDS`).
Variables nuevas (solo nombres): `IOT_BROKER_IDENTITY_MODE` (`off`/`record`;
`enforce` hace fallar el arranque), `IOT_BROKER_IDENTITY_HMAC_KEY`,
`IOT_BROKER_IDENTITY_HMAC_KEY_VERSION`, `IOT_BROKER_IDENTITY_HMAC_KEY_PREVIOUS`,
`IOT_BROKER_IDENTITY_HMAC_KEY_NEXT`, `MQTT_SESSION_EXPIRY_SECONDS`. Consumer y
worker deben compartir modo y anillo. Migraciones nuevas: `iot.0017`–`0019`
(0019 crea triggers: requiere `TRIGGER`). Detalle y rotación en
`docs/modules/iot-identidad-broker.md`.

