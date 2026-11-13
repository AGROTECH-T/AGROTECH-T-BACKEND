# Decisiones de arquitectura (ADR)

Índice de decisiones arquitectónicas aprobadas de AGROTECH-T. Cada ADR separa el
**estado de decisión** (aprobación arquitectónica) del **estado de implementación**
(disponibilidad funcional).

## Índice

| ID | Decisión | Estado de decisión | Estado de implementación |
|---|---|---|---|
| [DEC-01](DEC-01-desactivacion-logica-fincas.md) | Desactivación lógica de fincas | Aprobada | Implementada |
| [DEC-02](DEC-02-roles-por-membresia.md) | Roles por membresía | Aprobada | Implementada |
| [DEC-03](DEC-03-invitaciones-por-correo.md) | Invitaciones por correo | Aprobada | Implementada en backend; validación manual pendiente |
| [DEC-04](DEC-04-reactivacion-fincas.md) | Reactivación de fincas | Aprobada | Diseñada |
| [DEC-05](DEC-05-permisos-configurables.md) | Permisos configurables | Aprobada | Parcial |
| [DEC-06](DEC-06-ultimo-administrador.md) | Último administrador activo | Aprobada | Implementada |
| [DEC-07](DEC-07-historial-fincas-desactivadas.md) | Historial de fincas desactivadas | Aprobada | Implementada |
| [DEC-08](DEC-08-fijacion-digest-mariadb-orquestador.md) | Fijación por digest de la imagen MariaDB del orquestador | Aprobada | Implementada |
| [DEC-09](DEC-09-alcance-mutex-orquestador-desechable.md) | Alcance del mutex del orquestador desechable | Aprobada | Parcial |
| [DEC-10](DEC-10-riesgo-toctou-rutas-orquestador.md) | Riesgo TOCTOU residual en rutas del orquestador desechable | Aprobada | Parcial |
| [DEC-11](DEC-11-capacidad-global-crear-fincas.md) | Capacidad global para crear fincas y capacidades por finca | Aprobada | Implementada en código; base real pendiente |
| [DEC-12](DEC-12-fijacion-digest-redis-orquestador.md) | Fijación por digest de la imagen Redis del orquestador | Aprobada | Implementada |

## Diseños de módulo

| ID | Diseño | Estado de implementación |
|---|---|---|
| [FARMS-108](../modules/farms-108-invitaciones.md) | Invitaciones a fincas | Implementada en backend; validación manual pendiente |

## Formato de un ADR

Cada documento incluye:

- Identificador y título.
- Estado de decisión: Aprobada.
- Estado de implementación: Implementada, Parcial, Diseñada o Pendiente.
- Contexto.
- Decisión.
- Consecuencias.
- Trazabilidad al código cuando corresponda.
- Brechas de implementación.

No se deben inventar reglas de negocio agropecuario. Solo se registran decisiones
tomadas explícitamente.
