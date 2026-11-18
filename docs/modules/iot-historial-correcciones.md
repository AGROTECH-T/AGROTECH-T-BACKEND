# Historial inmutable y correcciones IoT

## Evidencia y valor efectivo

`Measurement` es la evidencia original y permanece inmutable. `value` y
`original_value` representan el valor decodificado original; `raw_value`, el
marcador de invalidez, métrica, fecha, sensor, finca, coordinador y trama se
conservan en la fila original. La ingesta sigue insertando filas, incluso con
`bulk_create`; no actualiza mediciones existentes.

`Measurement.value` es `DecimalField(max_digits=5, decimal_places=2)`, equivalente
a `DECIMAL(5,2)`, con rango representable `-999.99` a `999.99`. Las métricas
vigentes se decodifican como `raw_value / 100`; los sentinelas aprobados producen
`NULL`. Correcciones usan la misma precisión y escala, sin floats ni
redondeamiento. La entrada REST es una cadena decimal de hasta dos decimales.

Cada corrección es una nueva revisión append-only. `value_adjustment` fija el
valor efectivo a `corrected_value`; `invalidation` conserva la evidencia y
establece el valor efectivo a `null`. `value_before` captura el valor efectivo
inmediatamente anterior. `latest_correction` es la última revisión y
`effective_value` se deriva de ella, o de `original_value` si aún no hay
correcciones. El campo `value` existente mantiene su semántica original.

La API registra al usuario autenticado en `created_by`. `process_id` queda
reservado para procesos internos y no puede ser enviado por clientes REST. No
se exponen identificadores internos, datos brutos ni identificadores de
proceso.

## Concurrencia e integridad

El servicio bloquea primero la membresía administrativa de la finca y luego la
medición; bajo ese bloqueo calcula última revisión, secuencia y snapshot antes
de insertar. Las restricciones únicas y la FK compuesta de predecesor refuerzan
el orden. Los triggers MariaDB serializan la secuencia por medición con una
fila de cabeza de secuencia y rechazan actualizaciones/borrados de mediciones y
correcciones.

Los triggers también exigen sensores activos y, para sensores con fuente, una
fuente activa al insertar una medición; mantienen protección de identidad y
auditoría de metadatos de fuente. Los registros históricos con `Sensor.source`
NULL siguen admitidos. Ninguna fuente o sensor debe borrarse físicamente.

## API y permisos

- `GET /api/v1/iot/farms/{farm_id}/readings/{measurement_uid}/corrections/`:
  membresía de lectura IoT en la finca; devuelve valor original, valor efectivo
  y todas las revisiones.
- `POST` en la misma ruta: rol administrativo IoT; recibe
  `correction_type`, `corrected_value` y `reason`. El servidor asigna el actor.
- No existen rutas de edición ni borrado. Errores de dominio y validación siguen
  `{"error": "..."}`.

## Migraciones y despliegue

`0011_measurement_corrections` agrega `Measurement.uid` nullable, asigna UUIDs
por lotes a las filas existentes, verifica nulos/duplicados y luego impone
unicidad/no nulidad. No infiere procedencia física ni modifica valores. Crea
correcciones, secuencia y restricciones. La migración tiene una barrera de
reversión irreversible al final para evitar descartar historial y UUIDs.

`0012_iot_history_triggers` instala triggers y exige MariaDB 11.8. La DDL de
triggers produce commits implícitos, por lo que la migración es no atómica; los
nombres y definiciones preexistentes se verifican antes de crear o retirar
triggers. No se han ejecutado estas migraciones en este trabajo.

`0013_iot_ingestion_history_guards` agrega cuatro triggers `BEFORE UPDATE` y
`BEFORE DELETE` para `iot_received_frames` e `iot_frame_conflicts`. Cada uno
rechaza la operación con `SIGNAL SQLSTATE '45000'`; no intervienen en los
INSERT de la ingesta ni en la creación de conflictos. La instalación es
reintentable si una ejecución queda parcial: conserva los triggers propios ya
presentes, instala los ausentes y se detiene ante un nombre existente con otra
definición. No reemplaza objetos desconocidos.

El rollback ordinario de 0013 sigue rechazado: su reversión solo existe tras la
opción explícita `IOT_ALLOW_DESTRUCTIVE_HISTORY_ROLLBACK` (solo pruebas sobre
base desechable; ver `docs/modules/iot-validacion-02.md`). Retirar la
protección en un entorno real sigue requiriendo un cambio revisado y explícito. Los guards ORM bloquean `save()` de filas
existentes, `delete()`, `QuerySet.update/delete()` y `bulk_update`; aceptan
`create()` y `bulk_create()` normales, pero rechazan `ignore_conflicts` y
upsert. Estas capas no sustituyen los triggers ante SQL directo.

El perfil `config.settings_test_disposable_iot` limita `INSTALLED_APPS` a
accounts, farms, trazabilidad e IoT y autoriza el grafo hasta IoT 0013. El
preflight valida la configuración y la identidad real (base del RunId, usuario,
hostname, MariaDB 11.8.8, InnoDB y puerto interno 3306) antes de migrar. Este
perfil usa exclusivamente el puerto de host 3318; rechaza 3306, 3307 y 3317,
que permanece reservado al perfil desechable existente. El nombre de la base
no se considera evidencia suficiente de aislamiento.

## Límites de protección

Los triggers no protegen frente a una cuenta administradora capaz de cambiar el
esquema o eliminar triggers. `TRUNCATE` y operaciones DDL no pasan por los
triggers de fila; requieren privilegios de base de datos controlados. Las
relaciones históricas relevantes usan `PROTECT`/`RESTRICT`, pero el despliegue
debe limitar privilegios DDL y prohibir operaciones administrativas
destructivas. El contenido de triggers y la compatibilidad real con el servidor
requieren validación en MariaDB 11.8 desechable antes del despliegue. La
configuración IoT y el puerto separado están preparados en código, pero no se
han ejecutado ni conectado durante este BUILD.
