# IoT — SensorSource y procedencia física

`SensorSource` representa el componente físico que origina lecturas. `Sensor`
continúa representando el registro lógico de una métrica en un canal LoRa; cada
sensor lógico conserva su UID y las mediciones siguen referenciándolo. Una
fuente puede agrupar varios sensores lógicos.

## Modelo y ejemplo

Una fuente guarda UUID público, finca, coordinador, nombre, modelo opcional,
fabricante opcional, rol, descripción y ciclo de vida lógico. El rol admite
`primary` y `backup_comparison`; `NULL` representa una fuente histórica que no
puede clasificarse con evidencia. El servicio de alta exige un rol válido. Los
campos de modelo y fabricante permanecen `NULL` cuando no se confirmaron.

Para el ejemplo aprobado, una fuente BME680 con rol `primary` agrupa el canal 1
(`temperature`) y el canal 2 (`relative_humidity`). Una fuente DHT11 con rol
`backup_comparison` agrupa el canal 3 (`temperature`) y el canal 4
(`relative_humidity`). Cada canal se registra como un `Sensor` separado.

El rol es descriptivo. No implementa failover, selección de fuente efectiva,
comparación de lecturas ni alertas de discrepancia. La ingesta LoRa continúa
resolviendo lecturas mediante `channel + metric`; Frame v1 y el contrato MQTT no
cambian. Los sensores históricos con `source=NULL` conservan compatibilidad.

## Integridad y ciclo de vida

La FK compuesta de `SensorSource` garantiza que su coordinador pertenece a la
misma finca. Otra FK compuesta desde `Sensor` exige que fuente, coordinador y
finca coincidan. Se mantienen las FKs de finca/coordinador actuales, la
unicidad del canal activo y las claves compuestas que usa `Measurement`.

Desactivar una fuente la conserva y desactiva sus sensores lógicos activos en la
misma transacción. Sus mediciones permanecen. Reactivar la fuente no reactiva
los sensores; cada sensor requiere su operación de reactivación. Una sustitución
física usa una fuente nueva y sensores lógicos nuevos, sin reasignar lecturas
históricas.

UUID, finca y coordinador de una fuente son inmutables. Después de la primera
medición asociada, nombre, modelo, fabricante y rol no se pueden modificar. La
descripción se puede actualizar y cada cambio conserva snapshots antes/después,
actor y fecha en `iot_sensor_source_changes`. La reasignación de `Sensor.source`
se rechaza cuando el sensor ya tiene mediciones.

## Datos existentes

La migración agrega `Sensor.source` nullable y no crea fuentes ni infiere
procedencia desde UID, canal, métrica o antigüedad. Los datos previos quedan con
`source=NULL`. Un futuro backfill requiere inventario confirmado y no está
incluido aquí.

## API

- `GET /api/v1/iot/farms/{farm_id}/sensors/` incluye `source` con `uid`, `name`,
  `model`, `manufacturer`, `role` y `description`; devuelve `null` para sensores
  históricos sin procedencia registrada.
- `GET /api/v1/iot/farms/{farm_id}/sensor-sources/` lista fuentes activas de la
  finca autorizada.
- `POST /api/v1/iot/farms/{farm_id}/sensor-sources/` crea una fuente. El cliente
  envía coordinador, nombre, rol y metadatos opcionales; el backend genera el
  UUID.
- `PATCH /api/v1/iot/farms/{farm_id}/sensor-sources/{source_uid}/` actualiza
  metadatos dentro de las reglas de inmutabilidad.
- `POST /api/v1/iot/farms/{farm_id}/sensor-sources/{source_uid}/deactivate/`
  realiza baja lógica y desactiva sus sensores activos.

La lectura exige membresía activa; crear o modificar exige el permiso
administrativo IoT existente. No hay eliminación física.
