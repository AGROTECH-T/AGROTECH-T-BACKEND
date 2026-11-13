# AGROTECH GUAVIARE — Cierre técnico de Farms v1

**Estado:** APTO CON OBSERVACIONES
**Rama:** `feature/farms-core`
**Versión:** Farms v1
**Fecha de cierre técnico:** 2 de noviembre de 2026

> **Actualización FARMS-002:** la baja lógica de fincas y la autorización de
> escritura de Inventario sustituyen las limitaciones 4.1 y 4.2 de este
> documento. El contrato vigente está en `docs/api/farms.md`.
>
> **Actualización FARMS-002.2:** las escrituras de edición de fincas e
> Inventario bloquean la fila de la finca y revalidan membresía, estado y rol
> dentro de la transacción. Las pruebas de concurrencia están incorporadas, pero
> su ejecución queda pendiente de una base aislada autorizada.

## 1. Objetivo

Registrar la finalización técnica de la primera versión del módulo de fincas de AGROTECH GUAVIARE, documentando las funcionalidades implementadas, las verificaciones realizadas y las limitaciones que deberán atenderse en fases posteriores.

Este cierre se limita al alcance implementado. No representa una aprobación integral para producción. FARMS-002 incorporó posteriormente la autorización por rol y la baja lógica; el estado vigente está en `docs/api/farms.md`.

## 2. Alcance completado

Farms v1 incorpora:

- Creación de fincas.
- Creación transaccional de la finca y de la membresía administradora inicial.
- Listado de las fincas asociadas al usuario autenticado.
- Consulta del detalle de una finca, restringida por membresía.
- Validación del nombre en el serializer y en el servicio de dominio.
- Rutas integradas bajo `/api/v1/`.
- Modelos y relaciones para fincas, membresías y roles.
- Pruebas automatizadas de API, modelos, servicios y protecciones del entorno de pruebas.
- Documentación del módulo y de su integración.

## 3. Evidencias de verificación

Según los resultados de ejecución compartidos durante la revisión:

| Verificación | Resultado |
|---|---|
| Suite completa de pruebas | 111 aprobadas, 0 fallidas |
| Cobertura global | 95,93 % |
| Cobertura de líneas de los archivos medidos de Farms | 100 % |
| Umbral mínimo de cobertura | 80 %, superado |
| Ruff en `apps/farms/services.py` y `apps/farms/tests/test_services.py` | Sin errores |
| Revisión de migración inicial | Revisada manualmente frente a los modelos |
| Revisión de aislamiento entre usuarios y fincas | Sin defectos críticos observados en el alcance auditado |
| Revisión de atomicidad de creación | Correcta según código y pruebas revisadas |

**Nota:** estos resultados proceden de las ejecuciones y de la auditoría documentadas durante el desarrollo. No se ejecutaron pruebas ni se abrió una conexión a MariaDB durante la auditoría final.

La migración inicial fue revisada manualmente. Todavía no se ha confirmado automáticamente la ausencia de deriva mediante `makemigrations --check`.

## 4. Limitaciones aceptadas para esta versión

### 4.1. Autorización por roles

> **Antecedente histórico (sustituido por FARMS-002):** el estado descrito en
> esta sección ya no representa el comportamiento vigente. Los roles ya aplican
> autorización en la edición de fincas y en la escritura de Inventario.

Los roles se almacenan y se devuelven, pero todavía no aplican autorización diferenciada sobre las operaciones.

La implementación de permisos por rol queda como una fase independiente, coordinada con el responsable de esa funcionalidad.

Hasta entonces, no deberán añadirse endpoints de edición, eliminación, invitaciones, cambios de rol ni administración de membresías que dependan de permisos aún no implementados.

### 4.2. Ciclo de vida de fincas y membresías

> **Antecedente histórico (sustituido parcialmente por FARMS-002):** la baja
> lógica ya está implementada. El `CASCADE` y la política de fincas huérfanas
> siguen siendo riesgos para un eventual borrado físico, que no está expuesto
> por la API.

Las relaciones actuales utilizan eliminación en cascada. No existe todavía una política definitiva para evitar o gestionar fincas sin miembros.

Antes de implementar operaciones de borrado o administración de membresías, se deberá definir qué ocurre cuando se elimina el último miembro, incluyendo las reglas de propiedad, transferencia o eliminación de la finca.

### 4.3. Endurecimiento del entorno de pruebas

> **Actualización FARMS-002.2:** se añadieron pruebas de concurrencia para
> desactivación y escrituras competidoras. Su ejecución sigue pendiente hasta
> disponer de una base aislada autorizada.
>
> **Actualización FARMS-002.4:** la prueba `test_deactivation_waits_for_inflight_update`
> fue fortalecida para demostrar la espera por el bloqueo (conexiones independientes
> por hilo, señal verificable previa a la adquisición y comprobación determinista del
> estado `LOCK WAIT` acotada por `CONNECTION_ID()`). El aprovisionamiento y la
> protección del entorno de validación quedan en
> `docs/modules/farms-validation-environment.md`. La ejecución real sigue pendiente.
>
> **Actualización FARMS-101:** se retiró la dependencia de `information_schema.innodb_trx`
> (que exigiría el privilegio global `PROCESS`). La exclusión mutua se demuestra ahora
> de forma determinista por coordinación de eventos y una espera acotada sobre el evento
> de adquisición. El procedimiento de validación documenta la aplicación inicial de
> migraciones antes de `pytest --reuse-db`, restringe el usuario a `127.0.0.1` y añade
> `DROP` acotado a la base de validación para el `TRUNCATE` de los tests transaccionales.

La configuración de pruebas valida la base autorizada y exige el uso de `--reuse-db`. Sin embargo, quedan pendientes medidas adicionales para endurecer la protección ante condiciones de carrera y comandos alternativos.

Se recomienda revisar los privilegios del usuario de pruebas y retirar permisos `CREATE` y `DROP` innecesarios, además de mantener prohibido el uso de comandos de prueba que eludan las protecciones del proyecto.

### 4.4. Pruebas y controles pendientes

Como mejoras posteriores se registran:

- Prueba explícita de un usuario perteneciente a otra finca que intenta consultar una finca ajena.
- Pruebas adicionales de usuarios inactivos y del orden del listado.
- Pruebas de ciclo de vida y cascadas, una vez definida la política correspondiente.
- Verificación automatizada de deriva de migraciones en CI.
- Evaluación de cobertura de ramas, si se decide incorporarla a la estrategia de calidad.

Estos puntos no bloquean el cierre de Farms v1 dentro del alcance actual, pero deberán considerarse antes de ampliar las operaciones o preparar una versión productiva.

## 5. Decisión de cierre

Se considera **APTO CON OBSERVACIONES para cerrar técnicamente Farms v1** porque las funcionalidades previstas en su alcance actual cuentan con implementación, pruebas y documentación, y la auditoría no identificó defectos críticos ni regresiones en ese alcance.

Este cierre no equivale a declarar terminado el sistema configurable de permisos por módulo ni todas las políticas de ciclo de vida. Las limitaciones descritas se mantienen como condiciones explícitas para las siguientes fases.

## 6. Acciones posteriores

1. Revisar el estado de Git y separar los cambios propios de Farms de cualquier trabajo preexistente.
2. Mantener y enlazar este registro desde la documentación del módulo, sin sobrescribir contenido existente.
3. Coordinar la fase de autorización por roles. **Sustituido por FARMS-002.**
4. Definir la política de fincas huérfanas antes de implementar operaciones de borrado o administración de membresías. **La baja lógica fue implementada en FARMS-002; la política de borrado físico sigue pendiente.**
5. Planificar el endurecimiento de la base de pruebas y la comprobación automatizada de migraciones.
6. Preparar el commit de cierre únicamente después de revisar el contenido exacto que se incluirá.

## 7. Estado de Git

El cierre técnico no implica que se haya realizado un commit o un merge.

La rama `feature/farms-core` deberá permanecer intacta hasta que se revisen los cambios que se incluirán en el commit y se confirme que no se incorporará trabajo ajeno a Farms v1.
