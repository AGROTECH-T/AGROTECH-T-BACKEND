# DEC-02 — Roles por membresía

- **Identificador:** DEC-02
- **Título:** Roles por membresía
- **Estado de decisión:** Aprobada
- **Estado de implementación:** Implementada
- **Fecha:** 2026-11-07

## Contexto

Una cuenta puede pertenecer a varias fincas y desempeñar un rol distinto en cada
una. Un rol global de cuenta no representa correctamente esa relación ni permite
aislar la autorización entre fincas.

## Decisión

El rol pertenece a la **relación entre la cuenta y la finca** (membresía), no a la
cuenta global. Los roles son:

- `ADMIN` (interno: `administrador`).
- `OPERATOR` (interno: `operador`).
- `CONSULTATION` (interno: `consulta`).

Los roles funcionan como **techo de permisos**: delimitan el conjunto máximo de
permisos operativos que una membresía puede tener (ver DEC-05). La API devuelve
el valor interno.

## Consecuencias

- El creador de una finca recibe automáticamente el rol `administrador` en esa
  finca.
- El rol de una finca no otorga acceso a otra finca.
- La autorización estructural de administración (gestionar integrantes, roles,
  invitaciones, estado de la finca) se basa en el rol.
- La autorización operativa de módulos se calcula con el catálogo de permisos.

## Trazabilidad al código

- Modelo: `apps/farms/models.py` — `FarmRole`, `FarmMembership.role`.
- Catálogo operativo: `apps/permissions/catalog.py` — `role_ceiling`.
- Contrato: `docs/api/farms.md` — "Roles de membresía".

## Brechas de implementación

- Ninguna brecha funcional para el alcance aprobado.
- El mapeo entre los nombres de la decisión (`ADMIN`/`OPERATOR`/`CONSULTATION`) y
  los valores internos (`administrador`/`operador`/`consulta`) debe mantenerse
  documentado para evitar divergencias.
