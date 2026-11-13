# DEC-07 — Historial de fincas desactivadas

- **Identificador:** DEC-07
- **Título:** Historial de fincas desactivadas
- **Estado de decisión:** Aprobada
- **Estado de implementación:** Implementada
- **Fecha:** 2026-11-07

## Contexto

Tras la baja lógica (DEC-01), el historial de una finca desactivada sigue siendo
información sensible. Debe poder consultarse para auditoría, pero solo por quien
tiene autoridad sobre la finca, y sin efectos secundarios sobre su estado.

## Decisión

- El historial de fincas desactivadas solo puede ser consultado por un **`ADMIN`
  activo** de esa finca.
- Consultar el historial **no reactiva** la finca ni modifica su estado.
- La consulta es de solo lectura y no produce efectos secundarios.

## Consecuencias

- Otros roles y usuarios ajenos no acceden al historial.
- La reactivación es una operación independiente (DEC-04) y nunca un efecto de la
  consulta.
- El contrato HTTP debe reflejar el endpoint existente.

## Trazabilidad al código

- Servicio: `apps/farms/services.py` — `list_farm_events`,
  `get_readable_membership` (permite fincas desactivadas si el rol es
  `ADMIN`).
- API: `apps/farms/urls.py` — `farms/<int:farm_id>/history/`;
  `apps/farms/views.py` — `FarmHistoryView`.
- Pruebas: `apps/farms/tests/test_history.py` —
  `test_history_does_not_reactivate_or_mutate`.

## Brechas de implementación

- Ninguna brecha funcional para el alcance aprobado.
- **Corrección documental:** `docs/api/farms.md` afirmaba que no existía endpoint
  para consultar el historial de una finca desactivada; el endpoint y el servicio
  sí existen. Se corrige en esta etapa.
