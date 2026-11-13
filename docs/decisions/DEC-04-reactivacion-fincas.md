# DEC-04 — Reactivación de fincas

- **Identificador:** DEC-04
- **Título:** Reactivación de fincas
- **Estado de decisión:** Aprobada
- **Estado de implementación:** Diseñada
- **Fecha:** 2026-11-07

## Contexto

Una finca desactivada lógicamente (DEC-01) puede necesitar volver a operar. La
reactivación es una operación sensible sobre el estado de la finca y debe quedar
restringida y auditada.

## Decisión

La reactivación de una finca puede realizarla **únicamente un `ADMIN` activo** de
esa finca. La operación debe revalidar el estado y la pertenencia, y dejar
trazabilidad.

## Consecuencias

- La reactivación no está disponible para otros roles.
- Consultar el historial de una finca desactivada (DEC-07) **no** reactiva la
  finca.
- La implementación de la reactivación queda fuera de esta etapa documental.

## Trazabilidad al código

- Permiso de presentación: `apps/farms/role_permisos_admin.py` —
  `reactivar_finca` (solo presentación; no implica disponibilidad).
- Estado de la finca: `apps/farms/models.py` — `Farm.is_active`.
- **No existe** servicio `reactivate_farm` ni endpoint de reactivación.

## Brechas de implementación

- No existe servicio ni endpoint de reactivación.
- `reactivar_finca` figura en el catálogo de presentación como condicionado aunque
  la funcionalidad no está implementada; no debe interpretarse como disponible.
