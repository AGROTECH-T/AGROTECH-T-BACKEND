# API REST IoT — E3-API

## Base y autenticación

Todas las rutas están bajo `/api/v1/iot/` y usan la autenticación DRF existente
(JWE o sesión). Cada consulta usa `farm_id` en la ruta; los servicios de IoT
verifican la membresía activa y el acceso a esa finca. La finca indicada nunca
se considera por sí sola prueba de autorización. Un usuario sin membresía
activa recibe `404`; los miembros activos con capacidad de lectura pueden
consultar. Las escrituras de fuentes requieren el rol administrador IoT
existente; la autorización se realiza en los servicios de dominio.

## Endpoints implementados

| Método y ruta | Permiso | Respuesta |
|---|---|---|
| `GET /api/v1/iot/farms/{farm_id}/gateways/` | Autenticación y lectura IoT en la finca | Lista paginada de gateways activos |
| `GET /api/v1/iot/farms/{farm_id}/coordinators/` | Autenticación y lectura IoT en la finca | Lista paginada de coordinadores activos |
| `GET /api/v1/iot/farms/{farm_id}/sensors/` | Autenticación y lectura IoT en la finca | Lista paginada de sensores activos |
| `POST /api/v1/iot/farms/{farm_id}/sensors/` | Administrador de la finca | Registra un sensor lógico (C-4) |
| `GET /api/v1/iot/farms/{farm_id}/coordinators/{coordinator_uid}/gateways/` | Autenticación y lectura IoT en la finca | Historial paginado de autorizaciones (C-2) |
| `POST /api/v1/iot/farms/{farm_id}/coordinators/{coordinator_uid}/gateways/` | Administrador de la finca | Autoriza un gateway para el coordinador (C-1) |
| `POST /api/v1/iot/farms/{farm_id}/coordinators/{coordinator_uid}/gateways/{gateway_uid}/revoke/` | Administrador de la finca | Revoca la autorización vigente (C-3) |
| `GET /api/v1/iot/farms/{farm_id}/sensor-sources/` | Autenticación y lectura IoT en la finca | Lista paginada de fuentes activas |
| `POST /api/v1/iot/farms/{farm_id}/sensor-sources/` | Administrador de la finca | Crea fuente y genera UUID en backend |
| `PATCH /api/v1/iot/farms/{farm_id}/sensor-sources/{source_uid}/` | Administrador de la finca | Actualiza metadatos respetando inmutabilidad |
| `POST /api/v1/iot/farms/{farm_id}/sensor-sources/{source_uid}/deactivate/` | Administrador de la finca | Baja lógica; desactiva sensores asociados |
| `GET /api/v1/iot/farms/{farm_id}/readings/` | Miembro con lectura IoT | Mediciones paginadas y valor efectivo |
| `GET /api/v1/iot/farms/{farm_id}/readings/{measurement_uid}/corrections/` | Miembro con lectura IoT | Historial inmutable de correcciones |
| `POST /api/v1/iot/farms/{farm_id}/readings/{measurement_uid}/corrections/` | Administrador IoT de la finca | Agrega una revisión de ajuste o invalidación |

Parámetros comunes: `page` (entero, empieza en 1) y `page_size` (entero de 1
a 100; predeterminado 20). DRF responde con `count`, `next`, `previous` y
`results`. Los parámetros desconocidos no alteran la consulta.

## Campos públicos

Gateways y coordinadores exponen `uid`, `name`, `location_label`, `is_active`,
`created_at` y `updated_at`. Sensores exponen `uid`, `channel`, `metric`, `unit`,
`is_active`, `coordinator_uid` y `source`. La fuente incluye `uid`, `name`,
`model`, `manufacturer`, `role` y `description`, o `null` si la procedencia
histórica no está confirmada. La unidad se obtiene del catálogo estable de
métricas. Las fuentes exponen también `coordinator_uid` y estado, sin campos de
finca ni auditoría interna.

Ejemplo de respuesta:

```json
{
  "count": 1,
  "next": null,
  "previous": null,
  "results": [
    {
      "uid": "GW-ABCDEFGHIJ",
      "name": "Bodega",
      "location_label": "Norte",
      "is_active": true,
      "created_at": "2026-11-13T15:00:00Z",
      "updated_at": "2026-11-13T15:00:00Z"
    }
  ]
}
```

## Errores

- `400`: parámetro de paginación malformado.
- `401`: autenticación ausente o inválida.
- `403`: miembro sin rol administrativo al intentar gestionar una fuente.
- `404`: finca inexistente, usuario sin membresía activa o página no válida.
- `405`: método HTTP no implementado para la ruta.
- `409`: conflicto de estado o intento de cambiar identidad tras mediciones.

Las respuestas de error siguen el formato existente `{"error": "..."}`.

Cada lectura conserva `value` con su significado original, y añade `uid`,
`original_value`, `latest_correction` y `effective_value`. Las correcciones no
reescriben la medición. El cuerpo POST acepta `correction_type`, `reason` y
`corrected_value`; los valores se envían como cadena decimal con hasta dos
decimales. Para `invalidation`, `corrected_value` debe ser `null`. El actor sale
de la sesión autenticada; el cliente no puede enviar actor ni `process_id`.
`PUT`, `PATCH` y `DELETE` no están implementados para correcciones.

## Autorizaciones coordinador-gateway y alta de sensores (BUILD-IOT-AUTHORIZATION-API-01)

Las vistas solo traducen HTTP: las reglas las aplican los servicios existentes
(`authorize_gateway`, `list_coordinator_authorizations`,
`revoke_gateway_authorization` y `create_sensor`). En las escrituras el orden es:
membresía de administrador (`_authorize_manage`), validación del cuerpo,
servicio y respuesta. Un no miembro recibe `404` aunque envíe datos inválidos;
un miembro sin rol administrador, `403`. Los cuerpos son cerrados: un campo
desconocido produce `400`.

**C-1. Autorizar** — `POST …/coordinators/{coordinator_uid}/gateways/`

```json
{"gateway_uid": "GW-XXXXXXXXXX"}
```

Respuesta `201`:

```json
{"coordinator_uid": "CO-XXXXXXXXXX", "gateway_uid": "GW-XXXXXXXXXX",
 "active": true, "authorized_at": "2026-01-01T00:00:00Z", "revoked_at": null}
```

Errores: `400` (cuerpo inválido), `403`, `404` (coordinador o gateway
inexistente, con formato inválido o de otra finca: no se revela su
existencia), `409` (dispositivo inactivo o autorización ya vigente). Un
duplicado, también concurrente, deja una sola autorización vigente
(`UNIQUE(coordinator, gateway, active_pair)`). Auditoría:
`IOT_GATEWAY_AUTHORIZED`.

**C-2. Historial** — `GET …/coordinators/{coordinator_uid}/gateways/`

Lectura IoT (no exige administrador). Lista paginada con el mismo formato de
C-1, vigentes y revocadas. `include_revoked=false` devuelve solo la vigente;
un valor no booleano produce `400`. Coordinador de otra finca: `404`.

**C-3. Revocar** — `POST …/coordinators/{coordinator_uid}/gateways/{gateway_uid}/revoke/`

Cuerpo opcional `{"reason": "…"}`; el motivo admite como máximo 200 caracteres
(límite del registro de auditoría; si se supera, `400`). Respuesta `200` con
`active: false` y `revoked_at`. No borra filas: el historial conserva la
autorización revocada y quién la revocó. Si no hay autorización vigente
(también en una segunda revocación): `404`. Auditoría:
`IOT_GATEWAY_AUTHORIZATION_REVOKED`. **Efecto esperado del dominio:** las
tramas recibidas por ese gateway después de la revocación pueden rechazarse
como `REJECTED_AUTHORIZATION`.

**C-4. Registrar sensor** — `POST …/sensors/`

```json
{"coordinator_uid": "CO-XXXXXXXXXX",
 "source_uid": "00000000-0000-0000-0000-000000000000",
 "channel": 1, "metric": "temperature"}
```

`metric`: `temperature` o `relative_humidity` (catálogo). `channel`: entero
dentro del rango de la columna (0-65535 en MariaDB). `source_uid` es
obligatorio: el sensor necesita **antes** una `SensorSource` activa del mismo
coordinador y finca (`POST …/sensor-sources/`); el alta del sensor no crea
fuentes. Respuesta `201` con los campos públicos del sensor; `unit` sale del
catálogo. Errores: `400` (canal o métrica no válidos, `source_uid` ausente o no
UUID, campo desconocido), `403`, `404` (coordinador o fuente inexistentes, de
otro coordinador o de otra finca), `409` (coordinador o fuente inactivos, o
canal ya ocupado por un sensor activo). Auditoría: `IOT_SENSOR_CREATED` con
`entity_type="iot_sensor"`. El `GET` de la misma ruta no cambia.

## Seguridad y pendientes

Los serializadores usan listas cerradas y no exponen campos de credenciales,
ciphertext, nonce, huellas, claves, datos binarios de trama ni detalles de otras
fincas. No hay eliminación física de fuentes. Las rutas de credenciales y
tramas también quedan pendientes para una fase que defina su contrato seguro y
su servicio de lectura. Las autorizaciones coordinador-gateway y el alta de sensores sí tienen
API (sección anterior); la desactivación y reactivación de sensores siguen
siendo solo servicios de dominio. No hay failover automático.
