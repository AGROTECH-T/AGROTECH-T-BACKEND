# Hardening del pipeline IoT durable (BUILD-BACKEND-IOT-HARDEN-01)

Estado: implementado y probado en MariaDB 11.8.8 desechable. **No validado con
Flespi, Layerbase ni ESP32. No apto para producción sin esa validación.**

## C2 — Ciclo de vida de entregas fallidas

Estados (sin cambios en el modelo): `RECEIVED`, `PROCESSING`, `RETRY_PENDING`,
`ACCEPTED`, `DUPLICATE`, `REJECTED`, `CONFLICT`, `DEAD_LETTER`, `RESOLVED`.

```
RECEIVED ──claim──▶ PROCESSING ──ok──▶ ACCEPTED | DUPLICATE | REJECTED | CONFLICT
                       │
                       ├─ fallo transitorio, quedan intentos ──▶ RETRY_PENDING ──(next_attempt_at)──▶ claim
                       ├─ fallo permanente ───────────────────▶ DEAD_LETTER
                       └─ intentos agotados (al fallar o al reclamar) ▶ DEAD_LETTER
DEAD_LETTER ──requeue (operador)──▶ RETRY_PENDING (presupuesto nuevo)
DEAD_LETTER ──discard (operador)──▶ RESOLVED
```

- Código: `apps/iot/services/mqtt_delivery_failures.py` (política, clasificación,
  registro) y `apps/iot/services/mqtt_delivery_ops.py` (inspección, resolución).
- El fallo lo registra **el worker que aún tiene el token de fencing**, en su
  propia transacción. Si el token ya no coincide (otro worker reclamó), no se
  escribe nada.
- Si el propio registro del fallo falla (p. ej. MariaDB caída), la fila queda en
  `PROCESSING` hasta vencer el lease; el siguiente claim incrementa
  `attempt_count` y, al alcanzar el máximo, el **claim** la pasa a `DEAD_LETTER`
  (`MAX_ATTEMPTS_EXCEEDED`). Así no hay reintentos indefinidos ni con caídas del
  worker.

### Política de reintentos

| Variable / argumento | Por defecto | Rango |
|---|---|---|
| `IOT_MQTT_DELIVERY_MAX_ATTEMPTS` / `--max-attempts` | 8 | 1–1000 |
| `IOT_MQTT_DELIVERY_RETRY_BASE_SECONDS` / `--retry-base-seconds` | 5 | 1–3600 |
| `IOT_MQTT_DELIVERY_RETRY_MAX_SECONDS` / `--retry-max-seconds` | 900 | base–86400 |

Espera tras el intento *n*: `min(max, base · 2^(n-1))`, con exponente acotado.
Se persiste en `next_attempt_at`; la causa en `last_error_code` /
`last_error_safe` (solo códigos y nombre del tipo de excepción, nunca payload).

### Clasificación

| Error | Tipo | Resultado |
|---|---|---|
| `DatabaseError` (salvo integridad), configuración, inesperado | transitorio | `RETRY_PENDING` |
| `IntegrityError`, `DataError` | permanente | `DEAD_LETTER` |
| `DeliveryPayloadMismatchError`, `IngestionContractError` | permanente | `DEAD_LETTER` |
| `MqttPayloadKeyUnavailable` (versión de clave no configurada) | operativo, reintentable | `RETRY_PENDING` + **pausa global** |
| `MqttPayloadCryptoError` (autenticación GCM fallida) | permanente | `DEAD_LETTER` + **pausa global** |
| `LeaseLostError` | sin registro | otro worker es dueño |

Los rechazos de dominio (HMAC, replay, payload inválido…) ya eran resultados
tipados de la ingesta y terminan en `REJECTED`; no cambian.

### FIFO y carriles

`DEAD_LETTER` sigue bloqueando su carril (coordinador autenticado, candidato o
carril sin coordinador): **nunca se salta una entrega fallida**. El bloqueo no es
silencioso: se registra a nivel ERROR, queda un evento `DEAD_LETTERED` y
`mqtt_deliveries status` muestra `blocked_successors`. Otros carriles siguen
fluyendo. `RETRY_PENDING` también retiene a sus sucesores durante el backoff.

### `attempt_count`

Sigue siendo `PositiveSmallIntegerField` (sin migración). El desbordamiento es
imposible por construcción: máximo configurable ≤ 1000 (< 32767), el claim
nunca incrementa una fila con el presupuesto agotado y `requeue` reinicia el
contador conservando el valor previo en `IotMqttDeliveryEvent.attempt_number`.

## Comandos de operación (`python manage.py mqtt_deliveries …`)

Solo lectura: `status`, `list`, `pauses`. Escritura auditada, una sola entrega o
un solo alcance: `pause`, `resume`, `resolve`. No hay endpoints HTTP.

```bash
python manage.py mqtt_deliveries status <uuid> --events
python manage.py mqtt_deliveries list --state retry_pending --limit 20
python manage.py mqtt_deliveries list --state dead_letter
python manage.py mqtt_deliveries pauses            # activas
python manage.py mqtt_deliveries pauses --all      # incluye cerradas
python manage.py mqtt_deliveries pause --global --reason OPS_MANTENIMIENTO --operator jdoe
python manage.py mqtt_deliveries pause --coordinator-id 42 --reason OPS_REVISION --operator jdoe
python manage.py mqtt_deliveries resume --global --reason OPS_FIN --operator jdoe
python manage.py mqtt_deliveries resolve <uuid> --action requeue --reason DB_RESTAURADA \
    --operator jdoe --confirm <uuid>
python manage.py mqtt_deliveries resolve <uuid> --action discard --reason OPS_CORRUPTA \
    --operator jdoe --confirm <uuid>
```

- `--confirm` debe repetir el UUID: no hay acciones masivas.
- `--reason`: ASCII, ≤ 64, solo letras, dígitos, `_ - .`.
- Las acciones quedan en `IotMqttDeliveryEvent` (`worker_id=ops:<operador>`) o en
  `IotMqttDeliveryPauseEvent` (`process_id=ops:<operador>`).
- Procedimiento ante `DEAD_LETTER`: `status --events` → diagnosticar
  `reason_code`/`last_error_code` → corregir la causa → `requeue` si debe
  procesarse, `discard` si se confirma inválida. Tras un error criptográfico,
  revisar claves, `resume --global` y luego resolver la entrega.

## S2 — Consumer MQTT

Comportamiento verificado de Paho 2.2.0: `disconnect()` desde `on_message`
deja `loop_forever` retornando con normalidad; antes el proceso salía con 0.

Ahora (`apps/iot/mqtt/consumer.py`):
- Fallo de persistencia → sin PUBACK, desconexión, y los mensajes que lleguen
  durante la detención no se persisten ni confirman. La sesión persistente
  (`clean_session=False`, QoS 1) los reentrega.
- Espera con backoff (`MQTT_RECONNECT_MIN_DELAY`…`MAX`), sondeo `SELECT 1` y
  reconexión controlada. Una persistencia correcta reinicia el contador.
- `MQTT_MAX_PERSISTENCE_FAILURES` (5) fallos consecutivos → `MqttConsumerFatalError`.
- `MQTT_MAX_CONNECT_FAILURES` (20) fallos de conexión consecutivos → fatal.
- Fin de bucle sin `stop()` → fatal. `mqtt_consumer` lo convierte en
  `CommandError` → **código de salida 1** (Render reinicia el worker).

## S3 — TLS obligatorio

Con `DJANGO_DEBUG=false` (o `production=True`), `MqttSettings.from_env` y
`mqtt_consumer --check` rechazan: `MQTT_TLS` distinto de `true`,
`MQTT_TLS_INSECURE=true` y `MQTT_CA_CERTS` inexistente. El cliente usa
`CERT_REQUIRED` y verificación de hostname. En pruebas aisladas
(`DEBUG=true` o `production=False`) se conserva MQTT sin TLS.

## S1 — Fecha de medición

`resolve_measured_at` en `apps/iot/services/ingestion.py`:
- `lora_rx_at` se usa solo si `clock_synced` y está en
  `[server_received_at − 30 días, server_received_at + 2 min]`.
- Fuera de rango (futuro u obsoleto) o sin sincronizar: `measured_at =
  server_received_at`, `measured_at_approximate=True`, aviso en log.
- El original siempre queda en `ReceivedFrame.gateway_received_at`.
- `server_received_at` es ahora la recepción durable (`IotMqttDelivery.received_at`),
  no la hora de procesamiento (que puede ser horas después tras reintentos).
- Frame v1 y contrato MQTT sin cambios. Compromiso aceptado: un dato
  bufferizado de más de 30 días se registra con la hora de recepción, marcado
  como aproximado.

## CI y privilegios de migración

`.github/workflows/ci.yml` concedía `SELECT, INSERT, UPDATE, DELETE, CREATE,
ALTER, INDEX`; las migraciones IoT requieren además `DROP`, `REFERENCES`
(FK compuestas 0002/0008/0010/0011) y `TRIGGER` (0012/0013). Se añadieron,
solo sobre la base aislada `agrotech_farms_validation` del contenedor de CI.
El contenedor no activa binlog, por lo que no requiere `SUPER`. Nada se concede
a Layerbase.

## Validación posterior con Flespi (pendiente, requiere autorización)

1. Entorno de staging autorizado, `DJANGO_DEBUG=false`, `MQTT_TLS=true`, puerto
   TLS de Flespi, `MQTT_CLIENT_ID` fijo, token como credencial.
2. `python manage.py mqtt_consumer --check` debe responder "Configuración MQTT válida".
3. Publicar con un gateway de prueba un mensaje QoS 1 válido → fila `RECEIVED`
   → worker → `ACCEPTED`.
4. Cortar la base durante una publicación → sin PUBACK, reentrega tras
   restablecer, una sola fila (`REDELIVERY`).
5. Forzar más de `MQTT_MAX_PERSISTENCE_FAILURES` → proceso termina con código 1
   y Render lo reinicia.
6. Comprobar que la sesión persistente de Flespi conserva los mensajes no
   confirmados durante la desconexión (límite de cola del plan).
