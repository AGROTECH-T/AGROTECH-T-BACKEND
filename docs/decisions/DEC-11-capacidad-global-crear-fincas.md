# DEC-11 — Capacidad global para crear fincas y capacidades por finca

- **Identificador:** DEC-11
- **Título:** Capacidad global para crear fincas y capacidades por finca
- **Estado de decisión:** Aprobada
- **Estado de implementación:** Implementada en código y validada en entornos desechables; base real pendiente
- **Alcance:** BUILD F6 y F6-CORRECCIONES-1
- **Fecha:** 2026-11-12
- **Relación:** DEC-02 (roles por membresía), DEC-05 (permisos configurables), DEC-06 (último administrador)

## Contexto

Los roles de AGROTECH-T pertenecen a la membresía cuenta–finca (DEC-02); no
existe un rol global. Antes de F6 cualquier cuenta autenticada podía crear una
finca. La decisión de negocio es que solo ciertas cuentas creen fincas, con
independencia del rol que tengan en otras fincas.

## Decisión

1. **Capacidad global `User.can_create_farms`** (booleano, por defecto `False`).
   - Es independiente del rol de membresía: ser administrador de una finca no la
     concede.
   - No se concede al aceptar invitaciones ni se pierde o recupera al abandonar
     una finca o cambiar de rol.
   - Una cuenta inactiva nunca puede crear fincas, aunque tenga el campo en `True`.
   - `apps.farms.services.create_farm` la exige y responde
     `403 {"error": "tu cuenta no tiene permiso para crear fincas"}`.
2. **Concesión solo por comandos de gestión**, sin endpoint HTTP:
   - `manage.py grant_farm_creation --identification <doc> --reason "<motivo>"`
   - `manage.py revoke_farm_creation --identification <doc> --reason "<motivo>"`
   - El motivo es obligatorio y no puede estar vacío.
   - Son idempotentes: si el valor no cambia, no escriben ni auditan.
   - Cambio y auditoría van en la misma transacción con bloqueo de fila; si la
     auditoría falla, el campo no cambia.
   - Eventos `farm_creation_granted` / `farm_creation_revoked` con `before`,
     `after`, `reason` y `metadata = {"executed_by": <usuario del SO>,
     "execution_source": "management_command"}`. `actor` es `None` porque no hay
     una cuenta de AGROTECH que represente al operador. **`executed_by` es una
     referencia de ejecución local, no una identidad autenticada de la
     plataforma**; si no puede obtenerse se registra `"desconocido"`.
3. **`GET /auth/me`** expone, solo lectura: `can_create_farms` y
   `can_open_inactive_farms` (la cuenta administra alguna finca, activa o no;
   habilita "Abrir por identificador" en la interfaz).
4. **Capacidades por finca** (`capabilities` en las respuestas de finca),
   calculadas en el servidor por `apps/farms/capabilities.py` a partir del rol,
   el estado de la membresía, de la finca y de la cuenta. Son **ocho** claves:
   `can_edit_farm`, `can_deactivate_farm`, `can_reactivate_farm`,
   `can_view_history`, `can_manage_members`, `can_manage_invitations`,
   `can_read_inventory`, `can_write_inventory`.
   - Son informativas para la interfaz; la autorización real sigue en cada
     servicio. Nunca se aceptan desde el cliente.
   - `INVENTORY_WRITE_ROLES` es la fuente única de los roles que escriben
     inventario (también la usa `apps.inventario`).
5. **`can_leave_farm` queda excluida deliberadamente.** `leave_farm` permite
   salir a un administrador solo si no es el último administrador activo
   (DEC-06); calcularlo exige contar administradores por finca y añadiría
   consultas por cada finca del listado. El servicio `leave_farm` es la única
   autoridad: la interfaz conserva su regla (finca activa) y **mostrar
   "Abandonar" no garantiza que la operación termine**; el backend puede
   responder `409`.
6. Los permisos granulares de `apps/permissions` siguen sin activarse (DEC-05).

## Consecuencias

- Tras migrar una base real, ninguna cuenta puede crear fincas hasta que se le
  conceda la capacidad con el comando.
- Las cuentas nuevas no crean fincas: la interfaz explica que deben recibir una
  invitación.
- La interfaz prioriza `capabilities`; si una copia local de la finca cambia de
  estado o de rol sin releerla, se descartan las capacidades antiguas.

## Enmienda F10.2 — Primera finca desde el onboarding

- **Estado:** implementada en código y validada en el entorno desechable; base
  real pendiente.
- **Cambio:** una cuenta activa y con correo verificado que no tiene
  `can_create_farms` puede crear **su primera finca**. Al hacerlo queda como
  administradora (membresía existente) y recibe `can_create_farms=True` en la
  misma transacción, con un evento `farm_creation_granted` cuyo `actor` es la
  cuenta y `metadata.execution_source = "first_farm_onboarding"`.
- **Correo verificado:** `create_farm` lo exige siempre; `can_create_farms` en
  `/auth/me` también lo incluye. La bandera de las cuentas existentes no se toca.
- **Sin bandera nueva (sin migración):** la excepción se deriva del historial
  persistente (`capabilities.first_farm_available`). Deja de estar disponible si
  la cuenta tiene o tuvo una membresía de administrador en cualquier estado
  (cubre fincas creadas antes de que se auditara su creación), figura como
  autora de `farm_created` o tiene cualquier evento `farm_creation_granted` /
  `farm_creation_revoked`. Las membresías no se borran y `AuditEvent` es de solo
  inserción, así que no puede reactivarse ni eludir un `revoke_farm_creation`.
- **Concurrencia:** la cuenta se bloquea (`select_for_update`) antes de decidir;
  dos solicitudes simultáneas conceden la capacidad una sola vez.
- **Reintentos (F10.2-CORRECCIONES):** una solicitud que repite la operación de
  onboarding (mismo nombre, dentro de 10 minutos desde la concesión, sin otras
  fincas administradas) devuelve la finca ya creada en lugar de duplicarla. Las
  creaciones adicionales legítimas no se bloquean. Las cuentas que ya tenían la
  capacidad no se deduplican: una clave de idempotencia del cliente cambiaría el
  contrato de `POST /farms/` (y la lista de cabeceras CORS si viaja en cabecera)
  y queda pendiente de autorización.
- **Lo que no cambia:** los comandos de gestión, el rol por membresía (DEC-02),
  y que no existe administrador global: "administrador principal del espacio de
  trabajo" es la combinación de `can_create_farms` y la membresía de
  administrador de su finca; no hay `is_staff` ni `is_superuser`.
- **Consecuencia que sustituye a la anterior:** las cuentas nuevas verificadas sí
  pueden crear su primera finca; después, solo mientras conserven la capacidad.

## Trazabilidad al código

- `apps/accounts/models.py`, `apps/accounts/migrations/0006_user_can_create_farms.py`
- `apps/accounts/services/capability_service.py`
- `apps/accounts/management/commands/grant_farm_creation.py`, `revoke_farm_creation.py`
- `apps/farms/capabilities.py`, `apps/farms/services.py` (`create_farm`),
  `apps/farms/serializers.py`, `apps/farms/views.py`
- `apps/trazabilidad/models.py`, `apps/trazabilidad/migrations/0008_alter_auditevent_event_type.py`
- Pruebas: `apps/farms/tests/test_capabilities.py`,
  `apps/farms/tests/test_first_farm_f102.py` (F10.2)

## Brechas de implementación

- La base real no está migrada y ninguna cuenta tiene la capacidad.
- `can_leave_farm` no existe por decisión (punto 5).
