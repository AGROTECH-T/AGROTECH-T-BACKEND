# DEC-05 — Permisos configurables

- **Identificador:** DEC-05
- **Título:** Permisos configurables por módulo y acción
- **Estado de decisión:** Aprobada
- **Estado de implementación:** Parcial
- **Fecha:** 2026-11-07

## Contexto

La autorización operativa no puede depender de un conjunto fijo de permisos por
rol: cada finca necesita ajustar qué puede hacer cada integrante en cada módulo y
acción. A la vez, debe existir un techo por rol que impida elevar privilegios por
encima de lo que el rol permite.

## Decisión

Los permisos operativos se configuran por **módulo** y **acción**. El permiso
efectivo de una membresía se calcula con la fórmula:

`P_effective = (P_role ∩ P_assigned) − P_restricted`

Donde:

- `P_role` es el techo del rol de la membresía.
- `P_assigned` son las asignaciones explícitas de la membresía.
- `P_restricted` son las restricciones activas de la membresía.

### Fuente única y planos de autorización

- `apps.permissions/catalog.py` es la **fuente única** de autorización operativa.
- `apps/farms/role_*.py` queda **exclusivamente para presentación**; nunca es
  fuente paralela de autorización.
- La **autorización estructural de administración** (gestionar integrantes,
  roles, invitaciones, estado y reactivación de la finca) es independiente de la
  **autorización operativa** de módulos y acciones.

### Identificadores

- Los identificadores de permisos son `lowercase_snake_case`.
- Longitud máxima: 64 caracteres.

### Asignación y restricción

- Cualquier `ADMIN` activo puede administrar los permisos operativos de cualquier
  miembro activo, incluso de otro `ADMIN`.
- Un `ADMIN` puede restringirse a sí mismo, pero **no** puede elevar sus propios
  permisos.
- Un `ADMIN` puede retirar restricciones impuestas a otro `ADMIN`, con **motivo
  obligatorio** y auditoría.
- Las asignaciones y restricciones no pueden superar el techo del rol objetivo.

### Inicialización y reactivación

- La creación de una finca otorga **explícitamente** al `ADMIN` creador el
  conjunto de permisos permitido por su techo.
- Otros `ADMIN` **no** reciben automáticamente todos los permisos operativos:
  requieren asignaciones explícitas.
- Las membresías reactivadas después de `REVOKED` o `LEFT` comienzan **sin
  permisos operativos** y requieren nueva asignación administrativa.
- La ausencia de asignación implica **denegación** una vez completada la migración
  correspondiente.

### Creación y aprobación

- La creación y la aprobación son **permisos independientes**.
- La política de aprobación se configura **por finca y módulo**.
- La **autoaprobación** es configurable y está **desactivada por defecto**.
- Si no existe un aprobador elegible, se devuelve **HTTP 409**; nunca se aprueba
  automáticamente.
- Los registros pendientes se evalúan con la **política vigente al momento de
  aprobarlos**.
- Los registros aprobados **no** se modifican retroactivamente por cambios
  posteriores en la política.

### Alcance de acceso (Tickets)

El acceso a **Tickets** puede configurarse para:

- registros **asignados al miembro**, o
- **todos los registros de la finca**,

siempre sujeto a los **permisos efectivos del módulo y de la acción**.

La **identificación del dominio** al que pertenecen los Tickets y la **posible
creación de un módulo Tickets** quedan **pendientes de una decisión arquitectónica
posterior**. Esta decisión no define Tickets como módulo existente, ni entidades,
ni relaciones, ni amplía su alcance funcional.

### Procedencia y concurrencia

- La procedencia de las asignaciones debe conservar `source` y `migration_batch`.
- No debe usarse `granted_by=None` como sustituto de la procedencia migrada.
- Se mantiene el orden de bloqueos `Farm → Membership(s)` por PK.

## Consecuencias

- Un miembro sin asignación explícita no tiene permisos operativos una vez
  completada la migración.
- Los cambios de política no alteran retroactivamente registros ya aprobados.
- La administración de permisos es transaccional y auditada.

## Trazabilidad al código

- Catálogo: `apps/permissions/catalog.py` — `Permission`, `PERMISSION_CATALOG`,
  `role_ceiling`, validación `lowercase_snake_case` ≤ 64.
- Modelos: `apps/permissions/models.py` — `PermissionAssignment`,
  `PermissionRestriction`, `ApprovalPolicy`, `AssignmentSource`,
  `migration_batch`.
- Servicios: `apps/permissions/services.py` — `assigned`, `restricted`,
  `effective`, `has`, `grant`, `restrict`, `ungrant`, `unrestrict`, orden de
  bloqueos y auditoría.
- Presentación: `apps/farms/role_*.py` (no autorización).

## Brechas de implementación

- El motor de cálculo (`effective`) y la administración de asignaciones y
  restricciones están implementados.
- **Pendiente:** flujo de aprobación y lógica de `ApprovalPolicy` (hoy solo modelo
  y pruebas de modelo).
- **Pendiente:** otorgamiento explícito inicial al crear la finca; `create_farm`
  crea la membresía pero no genera asignaciones.
- **Pendiente:** reactivación de membresías `REVOKED`/`LEFT` sin permisos.
- **Pendiente:** migración de datos de asignaciones y la regla "ausencia =
  denegación tras la migración".
- **Pendiente:** alcance de acceso de Tickets (dominio y eventual módulo).
- **Pendiente:** integración de `effective`/`has` en la autorización operativa de
  farms e inventario, que hoy usan comprobaciones fijas por rol.
- La autorización operativa y la estructural coexisten; la migración de la
  autorización por rol a permisos efectivos debe planificarse en la Unidad 7.
