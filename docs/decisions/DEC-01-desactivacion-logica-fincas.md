# DEC-01 — Desactivación lógica de fincas

- **Identificador:** DEC-01
- **Título:** Desactivación lógica de fincas
- **Estado de decisión:** Aprobada
- **Estado de implementación:** Implementada
- **Fecha:** 2026-11-07

## Contexto

Las fincas pueden dejar de operar sin que ello implique perder su información.
Un borrado físico destruiría relaciones e historial (membresías, inventario,
movimientos, auditoría) y dificultaría la trazabilidad. Se requiere una baja que
conserve los datos y bloquee la operación normal.

## Decisión

La baja de una finca es **lógica**, no física:

- Se conservan la finca y todas sus relaciones e historial.
- La finca incorpora los campos `is_active`, `deactivated_at` y `deactivated_by`.
- Al desactivar se registra la fecha y la cuenta responsable.
- La operación es idempotente: repetirla no cambia la fecha ni el responsable
  original.
- Con la finca inactiva se bloquean las **escrituras normales**; las lecturas
  operativas dejan de exponerla en el listado.
- No se expone borrado físico por la API.

## Consecuencias

- Los datos históricos permanecen consultables según DEC-07.
- Las escrituras deben revalidar el estado activo de la finca dentro de la
  transacción y bajo bloqueo de fila.
- La política definitiva de borrado físico queda fuera de esta decisión y sigue
  pendiente.

## Trazabilidad al código

- Modelo: `apps/farms/models.py` — `Farm.is_active`, `Farm.deactivated_at`,
  `Farm.deactivated_by`.
- Servicio: `apps/farms/services.py` — `deactivate_farm` (baja idempotente),
  `lock_active_membership` (revalidación de estado para escritura).
- API: `apps/farms/views.py` — `FarmDetailView.delete`.
- Contrato: `docs/api/farms.md` — "Desactivar una finca".

## Brechas de implementación

- Ninguna brecha funcional para el alcance aprobado.
- La política de borrado físico de fincas no está definida ni expuesta.
- El bloqueo de fila depende de MariaDB/InnoDB; las migraciones no fijan el motor
  de las tablas.
