# INT-01 — Diseño de integración `feature/iot-mvp-funcional` + `develop`

Fecha: 2026-11-18 · Autores del trabajo integrado: Israel (IoT, fincas, cuentas, trazabilidad) y Cristian (inventario, galpón, alertas, roles de plataforma)
Estado: **PROPUESTA**. No se ha modificado código, ramas ni bases de datos.
Método: los conflictos se obtuvieron mezclando en una **copia de prueba aislada** (fuera de las carpetas de trabajo).

---

## 0. Resumen

| | Backend | Frontend |
|---|---|---|
| Punto común (merge-base) | `7ba5165` (03-nov) | `5d1d364` (03-nov) |
| Commits de Cristian en `develop` | 72 | 87 |
| Commits de Israel en `feature/iot-mvp-funcional` | 18 | 9 |
| Conflictos de texto | **16 archivos** | **15 archivos** |
| Auto-mezclados que igual hay que revisar | 7 | 2 |
| Migraciones con dos ramas paralelas | `accounts`, `farms` | — |

**Hechos clave**
1. **`main == develop`** en ambos repos (mismo commit). En el frontend, Cristian forzó un redeploy en Render el 16-nov. Por eso se asume que **las migraciones de `develop` ya están en una base de datos real** y **no se tocan**.
2. **IoT no tiene conflictos**: `develop` no modificó `apps/iot` ni `src/features/iot`.
3. Pero **IoT depende de la versión de Israel** de `farms` (`capabilities.py`, `lock_active_membership`, estados de membresía), de `trazabilidad` (`AuditEvent`) y de `permissions`. **Nada de eso existe en `develop`**: allí `apps/trazabilidad` solo tiene `__init__.py` y `apps.py`.
4. Lo que choca es el trabajo **del 9 al 12 de noviembre** que nunca se integró: cuentas, sesiones, membresías, invitaciones y permisos. Cada uno resolvió lo mismo de forma distinta.

---

## A. Modelo de acceso unificado (3 niveles)

Principio: **una sola fuente de verdad por concepto**, sin dos sistemas que hagan lo mismo.

### Nivel 1 — Cuenta (`apps/accounts`)
| Concepto | Decisión | Origen |
|---|---|---|
| Roles de plataforma `is_superadmin`, `is_administrador` | Se conservan | Cristian (`0003_platform_roles`) |
| Panel de administradores (`configuracion/administradores/…`) | Se conserva | Cristian |
| Módulos visibles por cuenta (`modulos_visibles`) | Se conserva **y se agrega `IOT = "iot"`** a `MODULOS_COMPLETOS` (hoy: home, fincas, inventario, galpon, configuracion) | Cristian + ajuste |
| Verificación de correo, `LoginCode` con propósito y entrega | Se conserva | Israel |
| **Sesiones múltiples** con cookie, `session_key` y `closed_at` | Se conserva (es más completo). Las funciones de Cristian que asumen una sola sesión por usuario (`OneToOne`) se adaptan | Israel |
| `can_create_farms` | Se conserva como **permiso concedido** a una cuenta | Israel |
| **Regla única "puede crear finca"** | `accounts/services/access.puede_crear_finca(user) = is_superadmin or is_administrador or can_create_farms`. `farms.capabilities.can_create_farms` la **llama** en vez de duplicarla | Mezcla |
| Respuesta pública de la cuenta | Una sola función `cuenta_publica()` que devuelve los campos de ambos: `is_superadmin`, `is_administrador`, `modulos`, `email_verified`, `can_create_farms`, `can_create_first_farm`, `can_open_inactive_farms`. Se elimina `_account_payload()` | Mezcla |

### Nivel 2 — Membresía de finca (`apps/farms`)
| Concepto | Decisión | Origen |
|---|---|---|
| Estado de membresía (activa, revocada o retirada), `revoked_at`, `left_at` | Se conserva | Israel |
| Invitaciones por código y enlace, con código cifrado | Se conserva | Israel |
| Desactivación y reactivación de finca | Se conserva | Israel |
| Asignación de roles (`farms/asignacion.py`) | Se conserva, pero **debe respetar el estado** de la membresía (solo activas) | Cristian + ajuste |
| Primera finca desde el onboarding | Se conserva | Israel |

### Nivel 3 — Permisos dentro de la finca
| Concepto | Decisión | Origen |
|---|---|---|
| Permisos por rol `PERMISOS_POR_ROL` + permisos directos que **solo suman** (`FarmPermission`) | **Fuente única de permisos** | Cristian |
| Pantalla de roles y permisos (frontend `configuracion/`) | Se conserva | Cristian |
| `require_action()` de inventario y galpón | Se conserva | Cristian |
| Permisos IoT | Se **agregan al catálogo** de Cristian: `ver_iot` (todos los roles) y `gestionar_iot` (administrador, asignable) | Mezcla |
| `farms/capabilities.py` | Se reescribe como **adaptador**: calcula las capacidades leyendo `permisos efectivos`. `IOT_MANAGE_ROLES` e `INVENTORY_WRITE_ROLES` dejan de ser la fuente | Mezcla |
| `apps/permissions` (asignaciones, restricciones, políticas de aprobación) | **Se retira** (ver D) | — |
| Revocar o retirar una membresía | Borra sus `FarmPermission` (reemplaza a `_clear_membership_permissions` de `apps.permissions`) | Mezcla |

### IoT
Sin cambios funcionales. `apps/iot/services/access.py` pasa a pedir `gestionar_iot` / `ver_iot` al nivel 3. **El MVP sigue igual**: hoy solo el administrador gestiona IoT, y así queda.

---

## B. Resolución archivo por archivo

Leyenda: **I** = versión de Israel · **C** = versión de Cristian · **M** = mezcla

### Backend — conflictos (16)
| Archivo | Israel | Cristian | Resolución |
|---|---|---|---|
| `apps/accounts/models.py` | `Session` múltiple (FK, `id`, `session_key`, `closed_at`), `email_verified`, `can_create_farms` | `is_superadmin`, `is_administrador`, `Session.created_at` | **M**: `User` con los 4 campos; `Session` de **I** |
| `apps/accounts/api/urls.py` | Verificación de correo, CSRF | Rutas de administradores | **M**: todas las rutas |
| `apps/accounts/api/views.py` | `_account_payload`, vistas de verificación | `cuenta_publica` | **M**: vistas de **I** + `cuenta_publica` unificada (A, nivel 1) |
| `apps/accounts/api/serializers.py` | `VerificationConfirm`, `LoginResponse`, `CSRFToken` | `AdministradorFicha`, `ListaAdministradores`, `CrearAdministrador` | **M**: todos (sin solapamiento) |
| `apps/farms/services.py` | Ciclo de vida, invitaciones, primera finca, limpieza de `apps.permissions` | +3 líneas (prefetch de `extra_permissions`) | **I** + prefetch de **C**. La limpieza pasa a borrar `FarmPermission` |
| `apps/farms/tests/conftest.py` | Fixtures de membresías con estado | Fixtures con `FarmPermission` | **M** |
| `apps/farms/tests/test_models.py` | Pruebas de estado e invitación | Pruebas de `FarmPermission` | **M** |
| `apps/inventario/services/movements.py` | `require_write_membership` (finca activa + rol, con bloqueo) | Listados, ajustes con motivo, `require_action(WRITE_STOCK/ADJUST)`, producto inactivo | **C** + **chequeo de finca activa y membresía activa** de **I** dentro de `require_membership` |
| `apps/inventario/services/products.py` | Igual (finca activa) | CRUD completo, préstamos, código de barras, plantilla | **C** + chequeo de **I** |
| `apps/inventario/tests/conftest.py` | Membresías con estado | Permisos de acción | **M** |
| `apps/inventario/tests/test_services.py` | Pruebas de finca inactiva | Pruebas de API, reglas y galpón | **M** |
| `config/api_urls.py` | `v1/iot/` | `v1/` avicultura y alertas | **M**: las tres |
| `.env.example` | Bloque IoT/MQTT | (sin cambios en ese bloque) | **I** |
| `Dockerfile` | `CMD` solo gunicorn; migraciones aparte (IoT) | `CMD` con `migrate && gunicorn` y `/app/media` | **M**: se conserva `/app/media` de **C**; `migrate` **aparte** (ver E-3) |
| `pyproject.toml` | Reglas ruff de `permissions` y `trazabilidad` | Reglas de `avicultura` | **M** (se quita la de `permissions` si se retira) |
| `.github/workflows/ci.yml` | Modificado | **Borrado** el 11-nov ("quitar el chequeo automático") | **Pregunta F-2** |

### Backend — auto-mezclados que hay que revisar a mano (7)
`apps/farms/models.py` (deben convivir `FarmPermission` y `MembershipStatus`), `config/settings.py` (`INSTALLED_APPS` tiene `apps.permissions`: se quita si se retira), `apps/farms/tests/test_services.py`, `apps/accounts/management/commands/` (comandos de ambos), `requirements.txt`, `docs/architecture/architecture.md`.

### Frontend — conflictos (15)
| Archivo | Israel | Cristian | Resolución |
|---|---|---|---|
| `components/layout/NavIcon.tsx`, `navTypes.ts`, `tests/layout/navTypes.test.ts` | Ítem **IoT** | Módulos por rol, atajos, superadmin | **C** + ítem IoT visible si `modulos` incluye `iot` |
| `features/home/components/HomeScreen.tsx` | Ruta a `IotScreen` | Saludo, módulos, vistas | **C** + ruta IoT |
| `features/auth/types/index.ts` | `email_verified`, capacidades | `is_superadmin`, `is_administrador`, `modulos` | **M**: todos los campos |
| `features/auth/components/AuthScreen.tsx` | Verificación de correo (+262) | Botones de cambio entre formularios (+30) | **I** + botones de **C** |
| `services/api/client.ts` | Errores unificados, reintento con token vigente, renovación por actividad | `freshToken` (renueva a los 5 min), parámetro `cache` | **I** + parámetro `cache` de **C**. **Un solo** mecanismo de renovación (el de **I**, coherente con las sesiones del backend) |
| `features/farms/components/FarmsScreen.tsx`, `FarmDetail.tsx`, `FarmCreateForm.tsx`, `styles/farms.css` | Integrantes, invitaciones, historial, onboarding (+286/+503/+55/+377) | `BackButton` y ajustes visuales | **I** + `BackButton` y estilos de **C** |
| `features/inventario/components/InventarioBrowser.tsx`, `InventarioPanel.tsx`, `ProductDetail.tsx` | Respetar capacidades de la finca (+14 a +23) | Paginación, plantillas, etiquetas, préstamos (+345 a +530) | **C** + chequeo de permisos leyendo el **nivel 3** |
| `vite.config.ts` | Validación de `VITE_API_URL` y proxy (+28) | Pruebas `.tsx` | **M** |

### Frontend — auto-mezclados que hay que revisar (2)
`src/app/main.tsx`, `src/features/auth/services/session.ts` (comprobar que no queden dos lógicas de renovación).

---

## C. Plan de migraciones

**Regla:** las migraciones de `develop` **no se renombran ni se editan**. Las de Israel, que nunca se desplegaron, **se renumeran para ir después**.

### accounts
| Final | Archivo | Origen | Nota |
|---|---|---|---|
| 0001, 0002 | `0001_initial`, `0002_harden_sessions` | común | — |
| 0003 | `0003_platform_roles` | C | sin cambios |
| 0004 | `0004_session_created_at` | C | sin cambios (ya es idempotente: crea la columna solo si falta) |
| 0005 | `0005_logincode_purpose_user_email_verified…` | I (era 0003) | dependencia → `0004_session_created_at` |
| 0006 | `0006_session_multi` | I (era 0004) | **se quita** el `AddField created_at` (ya existe) y queda `AlterField` + el backfill. Mantiene: FK, `id`, `session_key`, `closed_at`, índice |
| 0007 | `0007_logincode_delivery_fields` | I (era 0005) | — |
| 0008 | `0008_user_can_create_farms` | I (era 0006) | — |

### farms
| Final | Archivo | Origen |
|---|---|---|
| 0002 | `0002_farmpermission` | C (sin cambios) |
| 0003 | `0003_farm_deactivation` | I (era 0002) |
| 0004 | `0004_farmmembership_status` | I (era 0003) |
| 0005 | `0005_farminvitation` | I (era 0004) |
| 0006 | `0006_farminvitation_code_hash` | I (era 0005) |

### Otras apps
- `trazabilidad 0001–0013`, `iot 0001–0021`: se conservan. **Se actualizan sus dependencias** que apuntan a nombres renumerados (hoy apuntan a `accounts 0003_logincode…`, `farms 0002_farm_deactivation`, `farms 0003_farmmembership…`).
- `permissions 0001`: se retira junto con la app.
- `inventario` y `avicultura`: las de Cristian, sin cambios.

### Comprobación obligatoria
`makemigrations --check --dry-run` sin cambios pendientes y `migrate` desde cero en una BD vacía (MariaDB 11.8). Después, `migrate` sobre **una copia** con el esquema de `develop` para simular producción. Lo crítico ahí es `accounts 0006`, que convierte `Session` de `OneToOne` a FK con datos reales.

### Base de datos local de Israel
Tiene aplicadas las migraciones con los **nombres viejos**. Recomendación: **recrearla** (es de desarrollo). Costo:
- Crear tu usuario y finca.
- **Volver a aprovisionar IoT** con `tools/iot/mvp_iot_provision.py`. Cambia el UID del gateway, así que hay que actualizar el ACL del token del gateway en Flespi, la línea `SET` del gateway (UID) y la del coordinador (clave nueva).
- Repetir `SET GLOBAL log_bin_trust_function_creators=1` antes de migrar (triggers de `iot 0019`).

Alternativa: renombrar las filas de `django_migrations`. **No se recomienda**: es SQL manual sobre el historial.

---

## D. Qué se retira y qué se pierde

| Se retira | Por qué | Qué se pierde |
|---|---|---|
| `apps/permissions` (`PermissionAssignment`, `PermissionRestriction`, políticas de aprobación, `catalog.py`) | Duplica el nivel 3 de Cristian, que es el que usan la UI, el inventario y el galpón | Las **restricciones** (quitar un permiso que el rol sí da) y las **políticas de aprobación**. Hoy ninguna pantalla las usa |
| `_account_payload()` | Reemplazada por `cuenta_publica()` unificada | Nada (los campos se conservan) |
| `freshToken` del frontend | Duplica la renovación de sesión | Nada (la renovación de Israel cubre el caso) |
| Pruebas de `apps/permissions` y `farms/tests/test_membership_permission_cleanup.py` | Se reescriben contra `FarmPermission` | — |

Si en el futuro hacen falta restricciones, se agregan **dentro** del nivel 3 de Cristian (por ejemplo `FarmPermission.mode = grant|deny`), no como segunda app.

---

## E. Riesgos y pruebas de aceptación

### Riesgos
1. **`Session` de `OneToOne` a FK en producción**: cambia la clave primaria de la tabla `sessions`. Hay que probarlo sobre una copia del esquema de producción y aceptar que **las sesiones abiertas se cierren** (los usuarios vuelven a iniciar sesión).
2. **Login de Cristian sobre sesiones múltiples**: confirmado. `apps/accounts/services/session_service.py:15` crea la sesión con `Session.objects.update_or_create(user=…)`, que trata la sesión como **una por usuario**; además las líneas 53 y `farms/asignacion.py:101` borran **todas** las sesiones del usuario. Hay que adaptar cada uso al modelo de sesiones múltiples; si no, el login puede fallar (`MultipleObjectsReturned`) o cerrar sesiones de otros dispositivos.
3. **`migrate` dentro del `CMD` del Dockerfile**: con `iot 0019`, migrar exige el privilegio `TRIGGER` y, con binlog, `log_bin_trust_function_creators=1`. Si la BD de Render no lo permite, el contenedor **no arranca**. Propuesta: ejecutar `migrate` como paso separado del despliegue (pre-deploy command de Render) y dejar gunicorn en el `CMD`.
4. **Retirar `apps/permissions`** cuando su tabla ya existe en alguna BD: solo afecta a BDs locales de Israel. No está en producción.
5. **Finales de línea (CRLF/LF)**: se recomienda agregar `.gitattributes` (`* text=auto eol=lf`) **en un commit aparte**, antes de la mezcla, para no mezclar ruido con cambios reales.

### Pruebas que deben pasar antes del Pull Request
| # | Prueba | Criterio |
|---|---|---|
| 1 | `python manage.py check` y `makemigrations --check --dry-run` | Sin errores ni migraciones pendientes |
| 2 | `migrate` desde cero en MariaDB 11.8 | Las 4 apps nuevas y las de Cristian aplican en orden |
| 3 | `migrate` sobre una copia del esquema de `develop` | `accounts 0006` convierte las sesiones sin error |
| 4 | Suite backend completa (pytest) | 0 fallos. Hay que comparar contra las pruebas de Cristian y las de Israel |
| 5 | Frontend `npm run build` + `npm test` | 0 fallos |
| 6 | Manual: login, crear finca, invitar, aceptar invitación, asignar permisos (pantalla de Cristian), inventario y galpón | Funciona |
| 7 | **E2E IoT con las placas**: BME680 → LoRa → gateway → Flespi → Django → React | Medición `ACCEPTED` visible en la pantalla IoT |
| 8 | Un usuario sin `gestionar_iot` | No puede registrar dispositivos (403) pero sí ver lecturas |

---

## F. Preguntas para Cristian

1. **¿`main` y `develop` están desplegados en Render con una BD real?** ¿Esa BD tiene datos que se deban conservar, en especial `sessions`?
2. **CI**: lo quitaste el 11-nov. ¿Fue por fallos o por tiempo? ¿Lo reactivamos con la suite unificada?
3. **`migrate` en el `CMD`**: ¿estás de acuerdo con moverlo a un paso previo del despliegue por el riesgo E-3?
4. **Permisos**: ¿te parece bien que tu modelo (rol + `FarmPermission`) sea la fuente única y que se agreguen `ver_iot` / `gestionar_iot`?
5. **Sesiones**: ¿alguna parte de tu código (login, panel de administradores) depende de que haya **una sola** sesión por usuario?
6. **Membresías**: tu `asignacion.py`, ¿puede filtrar por membresías activas (`status="active"`)?
7. ¿Hay trabajo tuyo **sin subir** que también toque `accounts`, `farms` o la navegación?

---

## G. Ejecución propuesta (INT-02, requiere aprobación)

En la rama nueva `integracion/develop-iot-mvp` (desde `feature/iot-mvp-funcional`), con un commit por paso:
1. `chore: .gitattributes` (normalizar finales de línea)
2. `merge: develop` con conflictos resueltos según la sección B
3. `refactor(accounts): cuenta pública y regla única para crear fincas`
4. `refactor(farms): capacidades leídas de permisos efectivos; retirar apps.permissions`
5. `feat(permisos): ver_iot y gestionar_iot`
6. `fix(migraciones): renumerar accounts y farms después de develop`
7. Pruebas 1 a 8 de la sección E. **Solo si todas pasan**: push de la rama y Pull Request hacia `develop` para que Cristian lo revise. Nunca se hace push directo a `main` ni a `develop`.
