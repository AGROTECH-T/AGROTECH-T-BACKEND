# INT-02 — Informe de integración `develop` + `feature/iot-mvp-funcional`

Fecha: 2026-11-18 · Rama: `integracion/develop-iot-mvp` (backend y frontend) · Diseño: `INT-01-diseno.md`
Estado: **fases 1 y 2 terminadas**. Fases 3 (pruebas en tu PC) y 4 (PR) pendientes.

La integración se hizo en una **copia aislada**. `feature/iot-mvp-funcional`, tu base de datos, tu `.env` y las placas no se tocaron.

---

## 1. Commits

### Backend (11 commits sobre `feature/iot-mvp-funcional`)
| # | Commit | Qué hace |
|---|---|---|
| 1 | `chore: normalizar finales de linea con .gitattributes` | `* text=auto eol=lf` (los `.ps1` en CRLF) |
| 2 | `merge: integrar develop…` | Resuelve los 16 conflictos (INT-01 §B) |
| 3 | `fix(migraciones): ordenar accounts y farms despues de develop` | accounts 0005–0008 y farms 0003–0006; sin `created_at` duplicado |
| 4 | `refactor(accounts): regla unica para crear fincas y cuenta publica unificada` | `puede_crear_finca()` única; `cuenta_publica()` con capacidades; módulo `iot` |
| 5 | `feat(permisos): ver_iot y gestionar_iot` | En la matriz de Cristian, coherente con `PERMISOS_POR_ROL` |
| 6 | `refactor(farms): capacidades e IoT leen la fuente unica de permisos` | `capabilities` y `iot.access` usan rol + `FarmPermission` |
| 7 | `chore(docker): migraciones al arrancar configurables con RUN_MIGRATIONS` | Ver §2-2 |
| 8 | `test: autorizar migraciones de develop en el entorno aislado` | Orquestador desechable |
| 9 | `fix(inventario): bloquear la finca al crear productos y registrar movimientos` | Recupera una protección de la rama IoT (§3) |
| 10 | `test(inventario): usar unit_code y category_code de develop` | Pruebas de la rama IoT con la API nueva |
| 11 | `docs(integracion): diseno INT-01 e informe INT-02` | Estos documentos |

### Frontend (3 commits sobre `feature/iot-mvp-funcional`)
| # | Commit | Qué hace |
|---|---|---|
| 1 | `chore: normalizar finales de linea con .gitattributes` | — |
| 2 | `merge: integrar develop…` | Resuelve los 15 conflictos (INT-01 §B) |
| 3 | `test: adaptar pruebas al frontend integrado` | Pruebas que dependían de pantallas que cambiaron |

---

## 2. Diferencias con el diseño INT-01 (y por qué)

1. **`apps/permissions` NO se retiró: queda en modo heredado.** Retirarla obliga a cambiar el orquestador de pruebas aisladas (`tools/run_permissions_disposable.ps1`, `settings_test_disposable_*`, unas 900 líneas de pruebas). Hacerlo sin poder ejecutar esas pruebas era arriesgado. **La fuente de verdad de los permisos ya es la de Cristian**; `apps/permissions` no decide nada. Se puede retirar en un PR aparte cuando lo acuerden.
2. **`migrate` sigue en el `CMD` del Dockerfile por defecto** (`RUN_MIGRATIONS=1`). Moverlo a un "pre-deploy" de Render puede romper el despliegue si su plan no tiene esa opción. Con `RUN_MIGRATIONS=0` se desactiva cuando se migre aparte.
3. **`ci.yml`: se respetó su eliminación** en `develop`. Pregunta para Cristian (INT-01 §F-2).
4. **Inventario en el frontend**: se usan los paneles de Cristian **sin** el ocultamiento de botones por capacidad de la rama IoT. **El backend sí bloquea** (403) a quien no tiene permiso. Las pruebas de esas reglas se conservan.
5. Orden de commits: las migraciones van antes de los refactors (necesario para verificar el grafo).

---

## 3. Decisiones técnicas tomadas durante la resolución

| Tema | Decisión |
|---|---|
| Sesiones | Modelo de sesiones múltiples de la rama IoT. Los servicios de sesión ya eran los de esa rama, así que no hubo que adaptar el login de Cristian. Su `asignacion.cambiar_estado` borraba sesiones; ahora las **cierra** con `session_service.revoke_all` (queda historial) |
| Crear finca | Superadmin y administradores de plataforma crean **sin** verificar correo (los crea el superadmin); la bandera `can_create_farms` sigue exigiendo correo verificado; la primera finca (onboarding) no aplica a roles de plataforma |
| Módulos | `iot` visible para superadmin, administradores y miembros **activos**; `modulos_visibles()` ignora membresías revocadas |
| Permisos IoT | `ver_iot` (todos los roles) y `gestionar_iot` (administrador; **asignable** como permiso directo desde la pantalla de Cristian) |
| `asignacion.py` | Todas sus consultas solo consideran membresías **activas** |
| Revocar/retirar membresía | Borra sus `FarmPermission` |
| Inventario (backend) | Servicios de Cristian + **bloqueo de finca y membresía** al crear productos y registrar movimientos (una desactivación simultánea ya no los deja pasar) |
| Fincas (frontend) | Pantalla de la rama IoT, ahora controlable desde el menú lateral de Cristian (`view`/`tick`) |
| Crear finca (frontend) | Candado contra doble envío (IoT) + ventana y confirmación de cancelar (Cristian) en Fincas; en el onboarding sigue en línea |
| Sesión en el navegador | Un solo mecanismo de renovación (el de la rama IoT). Se retiró la renovación interna del cliente HTTP |
| Verificación de correo | Sin imagen de fondo, igual que hizo Cristian en `CodeStage` |

---

## 4. Verificación realizada en la VM

| Prueba | Resultado |
|---|---|
| Backend `manage.py check` | ✅ Sin problemas |
| Backend `makemigrations --check` | ✅ `accounts` y `farms` sin cambios pendientes, sin conflictos de grafo. ⚠️ `iot` propone una `0022` (Meta/constraints): **ya pasaba antes de integrar** (verificado en `98cd8ea`), no lo causa la mezcla |
| Backend recolección de pruebas | ✅ 3.074 pruebas, 0 errores de importación |
| Backend pruebas **sin** base de datos | ✅ **1.453 pasan** |
| Backend pruebas **con** MariaDB (1.614) | ⏳ Fase 3 en tu PC |
| Backend `ruff` en archivos modificados | ✅ Sin errores nuevos |
| Frontend `tsc -b` | ✅ |
| Frontend `vite build` | ✅ |
| Frontend `vitest` | ✅ **609 / 609** |
| Frontend `eslint` | ✅ |

---

## 5. Pendientes conocidos

1. **Pruebas con MariaDB** (fase 3): es donde pueden aparecer fallos de comportamiento entre las dos ramas.
2. `iot 0022` pendiente (previo a la integración): revisar con la versión de Django de tu PC.
3. `crear_persona` (Cristian): si la persona tuvo una membresía **revocada** en esa finca, crearla de nuevo choca con la restricción única. Habría que reactivar en vez de crear.
4. Retiro de `apps/permissions` (§2-1) y decisión sobre CI (§2-3).
5. Ocultar botones de inventario según permisos (§2-4).
