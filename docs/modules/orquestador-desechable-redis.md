# Orquestador desechable: Redis opcional (F10.3.4-A)

> Registro F10.3.4-A (2026-11-12). Describe la ampliación del orquestador
> desechable con un Redis propio por ejecución, para validar en F10.3.4-B el
> contador de intentos de códigos de invitación (`apps/farms/code_attempts.py`)
> con un Redis real y procesos independientes. **F10.3.4-A no ha ejecutado
> ningún contenedor ni descargado ninguna imagen**: solo hay pruebas con mocks y
> una renderización estática de Compose. Complementa la
> [guía operativa](orquestador-desechable-guia-operativa.md).

Las etiquetas de evidencia son las de la guía: **[Código]**, **[Pruebas]**,
**[Inferido]**, **[No verificado]** y **[Recomendación]**.

## 1. Alcance

- Nuevo modificador `-WithRedis`, **solo con `-Validate`**. Sin él, el
  comportamiento del orquestador no cambia **[Pruebas]**.
- Durante pytest, la caché `default` de Django pasa a ser el Redis desechable en
  lugar de LocMemCache. Las pruebas del contador comparten así un Redis real
  entre procesos.
- No cambia el código productivo, los endpoints ni los umbrales. No se generan
  migraciones.

## 2. Diseño

| Elemento | Valor (**[Código]**) |
|---|---|
| Archivo Compose | `docker-compose.disposable.redis.yml`, como segundo `-f` entre el compose base y el override de la invocación. |
| Imagen | `redis:7.4.12@<digest>`; el digest lo aporta `-RedisImageDigest` y el override lo fija. `compose up --pull never`. |
| Contenedor y hostname | `agrotech-permtest-<RunId>-redis` y `agrotech-redis-<RunId>`. |
| Red y proyecto | Los de MariaDB: `agrotech-permtest-<RunId>-net` y `agrotech-permtest-<RunId>`. |
| Etiquetas | Las tres base, `agrotech.invocation-id` y `agrotech.owner-proof`, como MariaDB. |
| Puerto | Solo `127.0.0.1:16379` → `6379`. |
| Autenticación | `--requirepass` con una contraseña aleatoria de 256 bits por ejecución. `redis-cli` la toma de `REDISCLI_AUTH` dentro del contenedor. |
| Persistencia | Ninguna: `/data` en `tmpfs`, `--save ""`, `--appendonly no`. |
| Bases | `--databases 2`. La base 0 es la caché de Django; la base 1 guarda la marca `agrotech:disposable:run-id` = RunId. |
| Caché de Django | `config.settings_test_disposable` construye `CACHES` con django-redis, `LOCATION` fija `redis://127.0.0.1:16379/0`, contraseña de `AGROTECH_DISPOSABLE_REDIS_PASSWORD` y timeouts de 5 s. `REDIS_URL` sigue forzada a vacío y no se usa. |

## 3. Controles antes de migrar y de ejecutar pruebas

En el orquestador (**[Código]** **[Pruebas]**):

1. Parámetros: `-WithRedis` sin `-Validate`, sin `-RedisImageDigest` o con un
   digest mal formado → 3. `-RedisImageDigest` sin `-WithRedis` → 3.
   `-CleanupOnly` con cualquiera de los dos → 3.
2. `docker-compose.disposable.redis.yml` debe existir dentro de la raíz física
   del repositorio → si no, 40.
3. Contenedor con el nombre de Redis del RunId ya existente → 50, sin crear nada.
4. Puerto 16379 ocupado en la comprobación previa → 50, `NO_RESOURCES_CREATED`.
5. Imagen `redis:7.4.12` no presente en local con el digest indicado → 10, sin
   `up` ni descargas.
6. `compose config` renderizado: exactamente los servicios `mariadb` y `redis`.
   Para `redis` se exigen imagen fijada, nombres, etiquetas, una sola
   publicación `127.0.0.1:16379->6379`, solo la red del proyecto y `/data` en
   `tmpfs`. Se rechazan volúmenes, `build`, `env_file`, `privileged`, etc. → 10.
7. Tras `up`: un contenedor Redis ajeno o ambiguo nunca se borra. La etapa
   queda en 50 y el código final es 42. Un volumen nombrado montado en Redis
   tampoco se borra (42). Si Redis no llega a crearse → 40, y se limpia lo
   propio.
8. Salud: Redis debe llegar a `healthy` → si no, 40.
9. Identidad de Redis → 10 sin ejecutar Python si falla:
   - la propiedad se revalida;
   - `docker port` debe dar exactamente `127.0.0.1:16379`;
   - sin contraseña, `PING` debe responder `NOAUTH`;
   - el orquestador escribe la marca del RunId en la base 1.

En Python (**[Código]** **[Pruebas]** con cliente falso):

10. Con `AGROTECH_DISPOSABLE_REDIS=1`, la importación de los settings exige
    confirmación, RunId y contraseña. Cualquier valor de la marca distinto de
    `1` o vacío se rechaza.
11. `validate_disposable_cache` exige:
    - un solo alias `default`;
    - el backend django-redis y la ubicación fija;
    - contraseña no vacía;
    - que `IGNORE_EXCEPTIONS` y `DJANGO_REDIS_IGNORE_EXCEPTIONS` no estén
      activos, para que un fallo de Redis no pase en silencio.
12. `validate_live_disposable_redis` exige `redis_version` 7.4.12, `tcp_port`
    6379 y la marca igual al RunId. Lo comprueba:
    - el preflight (`migrate` y `preflight --expect-schema migrated`), como
      comprobación `redis` con estado `REDIS_NOT_ALLOWED` → 10;
    - el plugin de pytest (`-p config.disposable_preflight`), antes de cargar
      las pruebas → `UsageError`, que el orquestador traduce a 30.

## 4. Estado, limpieza y recuperación

- **[Código]** `run-state.json` añade `RedisContainerName` y
  `Owned.RedisContainerId`. La integridad pasa a la versión **v2**, en la que el
  HMAC cubre también esos campos. Sin `-WithRedis`, el estado sigue siendo
  **v1**, idéntico al anterior.
- **[Pruebas]** Un estado v1 con campos de Redis añadidos se rechaza (50). Una
  versión v2 rebajada a v1, o un `RedisContainerId` alterado, invalidan el
  HMAC (50). Ninguno de estos casos borra nada.
- **[Código]** Orden de la limpieza: Redis, MariaDB, volúmenes anónimos
  registrados (solo cuando ya no queda ninguno de los dos contenedores) y la
  red. `Cleanup.Redis` y `Recovery.Redis` registran el resultado.
- **[Pruebas]** `-CleanupOnly` con un estado v2 auténtico elimina Redis,
  MariaDB y la red propios (0). Con un Redis ajeno, `RECOVERY_REFUSED` (50) y
  no se borra.

## 5. Evidencia de F10.3.4-A

| Prueba | Resultado |
|---|---|
| Pester (`tools/tests/run_permissions_disposable.Tests.ps1`), suite completa | 297 en total: 296 aprobadas, 0 fallidas, 1 omitida (symlink, requiere privilegio; la misma que antes). Incluye 43 pruebas nuevas de Redis. |
| Pester antes de añadir las pruebas nuevas (regresión con el código ya modificado) | 254 en total: 253 aprobadas, 0 fallidas, 1 omitida. |
| pytest sin base ni Redis (`tests/test_disposable_redis.py`, `tests/test_disposable_preflight.py`, `tests/test_settings_test_disposable.py`, `tests/test_settings_test_disposable_isolated.py`) | 265 aprobadas (36 nuevas), 0 fallidas. |
| `docker compose config` con los tres archivos reales y valores ficticios (solo renderiza; no crea recursos) | Servicios `mariadb,redis`. `Test-ComposeRenderedConfig` no informa ningún problema, ni con Redis ni en el modo actual sin Redis. |

## 6. Limitaciones y pendientes

- Las dos primeras limitaciones de F10.3.4-A quedaron **resueltas en
  F10.3.4-B** (sección 8): la ejecución con un contenedor real (healthcheck,
  `NOAUTH`, `SET` e `INFO server`) y el digest de `redis:7.4.12`, fijado en
  [DEC-12](../decisions/DEC-12-fijacion-digest-redis-orquestador.md).
- **[Inferido]** En la imagen oficial, el entrypoint ejecuta `redis-server` como
  usuario `redis` porque el primer argumento es `redis-server`. No comprobado:
  el campo `Config.User` del contenedor está vacío, porque el cambio de usuario
  lo hace el entrypoint.
- **[Recomendación]** En `-WithRedis`, todas las pruebas de la ejecución
  comparten la base 0 de Redis. `cache.clear()` vacía solo la base 0; la marca
  de la base 1 no se ve afectada.
- La exclusión entre ejecuciones sigue siendo la de DEC-09. El puerto 16379 es
  una barrera adicional, como el 3317.

## 7. Pendiente para F10.3.4-B

Ejecutado en F10.3.4-B; los resultados están en la sección 8.

## 8. Resultados de F10.3.4-B (2026-11-12)

### 8.1. Cómo se obtuvo la evidencia

- **Redis real.** Contenedor `redis:7.4.12@sha256:2e3eb114…a90`
  ([DEC-12](../decisions/DEC-12-fijacion-digest-redis-orquestador.md)) creado
  por el orquestador con `-Validate -WithRedis`. Se usó también MariaDB 11.8.8
  con el digest de DEC-08.
- **Procesos independientes.** `python -m apps.farms.tests.f1034b_redis_worker`,
  lanzado desde pytest. Cada proceso:
  - tiene su propio PID y su propio arranque de Django;
  - demuestra la identidad de MariaDB y de Redis desechables antes de operar;
  - instala el bloqueo de correo y HTTP;
  - espera la señal `GO` común para salir a la vez que los demás.

  Las solicitudes pasan por la pila DRF completa: throttles, vista y guard.
- **Mocks.** Solo en las pruebas Pester y en `tests/test_disposable_redis.py`.
  Ninguno de los resultados de esta sección procede de mocks.
- **Pruebas:** `apps/farms/tests/test_code_attempts_redis_f1034b.py` (14) y
  `apps/farms/tests/test_code_attempts_redis_down_f1034b.py` (1). Con
  LocMemCache se omiten.

### 8.2. Infraestructura real

| Comprobación | Evidencia |
|---|---|
| Healthcheck de Redis | Pasa de `starting` a `healthy`. `ExitCode 0` en el registro de salud. |
| Sin contraseña | `docker exec … redis-cli ping` devuelve `NOAUTH Authentication required.`. Desde Python, `AuthenticationError`. |
| Con la contraseña temporal | `SET agrotech:disposable:run-id <RunId>` en la base 1 devuelve `OK`. |
| `INFO server` | `redis_version` 7.4.12, `redis_mode` standalone, `tcp_port` 6379, `arch_bits` 64. |
| Persistencia | `save ""`, `appendonly no`, `databases 2`. `/data` en tmpfs; sin montajes. |
| Puerto | `docker port` devuelve exactamente `127.0.0.1:16379`. `inspect` muestra `6379/tcp` → `127.0.0.1:16379`. |
| Red | Redis y MariaDB solo en `agrotech-permtest-<RunId>-net`, que tiene etiquetas de propiedad. |
| Propiedad | Ambos contenedores con `agrotech.invocation-id` y `agrotech.owner-proof` de la invocación. |
| Preflight de Python | Comprobación `redis` en OK en `migrate`, en la verificación posterior y en `preflight --expect-schema migrated`. |
| Caché de Django | `LOCATION` `redis://127.0.0.1:16379/0`. Un valor escrito con `cache.set` lo lee un cliente Redis directo. `IGNORE_EXCEPTIONS` no está activo. |
| Correo y HTTP | Mailguard instalado; 0 intentos SMTP y 0 HTTP en todas las ejecuciones. |
| Limpieza | `CLEANUP_COMPLETED`, con `Redis`, `Container` y `Network` en `Removed`, en todas las ejecuciones. Incluso con Redis detenido por `SHUTDOWN`. |

### 8.3. Contador de intentos (Redis real y procesos independientes)

| Prueba | Resultado |
|---|---|
| A. Contador compartido | El proceso A hace 3 fallos y su contador vale 3. El proceso B, con otro PID, lee 3, hace 2 fallos en accept (contador 5) y recibe 429 con el código válido. pytest y un cliente Redis directo leen 6, porque el intento bloqueado también suma. |
| B. `begin()` concurrente | En 3 rondas de 16 procesos simultáneos: **5 permitidos y 11 bloqueados en cada ronda**, y el contador queda en 16 (exactamente un incremento por proceso). |
| B. HTTP concurrente | 10 procesos simultáneos (preview y accept, IP distintas): **exactamente 5×404 y 5×429**; contador 10. |
| C. Cuota compartida | Preview→accept y accept→preview, cada paso en un proceso distinto: el contador sube 1,2,3,4,5 y el código válido siguiente recibe 429. La invitación sigue `pending` y no hay membresía. |
| D. Hasta 5 en curso | 2×200, 1×409 y 2×404 simultáneos: el contador final es 2. Los 200 y 409 devuelven su unidad y ningún valor es negativo. |
| D. 10 en curso (D-3, comportamiento actual) | Esperables 3×200, 3×409 y 4×404. Se observaron 2×200, 0×409, 3×404 y 5×429: **4 solicitudes válidas o de miembro recibieron 429**. Contador final 8 = 3 fallos + 5 bloqueadas. Ningún valor negativo. |
| E. TTL | TTL inicial 900 s. Dos incrementos 2 s después dejan el TTL en 898 s: `INCRBY` no lo reinicia. |
| E. Expiración (ventana de 2 s solo en el proceso de pruebas) | 5 permitidos y el 6.º bloqueado. Tras caducar, las claves `count` y `start` no existen y el siguiente intento abre una ventana nueva (contador 1, TTL ≤ 2 s). |

### 8.4. D-1: liberación tardía sobre una ventana nueva — **reproducido**

Reproducción determinista con Redis real y caducidad real (ventana de 2 s solo
en pruebas):

1. Una petición antigua reserva su intento en W1.
2. W1 caduca.
3. Un fallo nuevo abre W2 con el contador en 1.
4. La petición antigua resulta válida y su `not_failed()` decrementa W2 a 0.
5. En W2 se evalúan **6 intentos fallidos** frente al límite de 5.

En la variante con el orden de la carrera dentro de `_increment` (`add` de la
nueva, `decr` de la antigua, `incr` de la nueva), el contador llega a **-1** y
el fallo nuevo queda en 0.

- **Impacto.** Cada petición válida en curso en el instante en que vence la
  ventana borra un fallo de la ventana siguiente. Explotarlo exige tener un
  código válido o ser miembro (respuestas 200/409) y coincidir con el
  vencimiento. La ganancia es de un intento extra por petición tardía.
- **Propuesta de corrección (no implementada).** Guardar en el guard la
  identidad de la ventana en `begin()` y decrementar solo si sigue siendo la
  misma, de forma atómica: por ejemplo, un script Lua que compare la clave
  `start` con el valor reservado antes de `DECR`, o claves de contador por
  ventana. Además, nunca bajar de 0.

### 8.5. D-2: `ScopedRateThrottle` (`invitation_accept`, 10/hora) — **reproducido**

En 3 ensayos, 15 procesos simultáneos de la misma cuenta, cada uno con una IP
distinta para que no actúe el límite de 30/hora por IP:

| Ensayo | Pasaron DRF | Historial DRF guardado | 404 | 429 |
|---|---|---|---|---|
| Ejecución 2 | 12, 13, 15 | 10, 10, 9 | 5 en cada uno | 10 en cada uno |
| Ejecución 3 | 12, 10, 15 | 10, 10, 8 | 5 en cada uno | 10 en cada uno |

- **Throttle de DRF.** Lee el historial, lo modifica y lo vuelve a escribir sin
  atomicidad. Pierde actualizaciones y deja pasar **más de 10** solicitudes
  (hasta 15 de 15).
- **Límite de 5 fallos.** Es atómico en Redis y **se mantuvo siempre**: 5×404.
- **Límite de 30/hora por IP.** No se ejerció en este ensayo (una IP por
  proceso). Usa el mismo mecanismo no atómico de DRF.
- **Propuesta (independiente, no implementada).** Un throttle propio con
  contador atómico en Redis (`INCR` + `EXPIRE`, o un script Lua de ventana
  deslizante) para `invitation_accept` e `invitation_accept_ip`, sin cambiar
  los umbrales.

### 8.6. F: Redis no disponible (comportamiento real)

| Caso | preview | accept | `CodeAttemptGuard.begin()` | Invitación |
|---|---|---|---|---|
| `CLIENT PAUSE` (Redis no responde; timeout de 5 s) | 500 | 500 | `redis.exceptions.TimeoutError` | `pending`, sin membresía |
| `SHUTDOWN NOSAVE` (conexión rechazada) | 500 | 500 | `redis.exceptions.ConnectionError` | `pending`, sin membresía |

- **Falla cerrado.** No se degrada a una caché local ni se evalúa el código sin
  protección. El 500 lo produce el primer acceso a caché, el de los throttles
  de DRF, antes de llegar a la vista.
- `RedisCache` vuelve a lanzar la causa original de `ConnectionInterrupted`.
- **[Inferido]** Si Redis falla entre una aceptación correcta y su
  `not_failed()`, la membresía ya creada quedaría con una respuesta 500. No se
  ha reproducido.

### 8.7. D-3: intentos válidos bloqueados bajo alta concurrencia (comportamiento actual)

`begin()` reserva la unidad antes de mirar el código. Con 6 o más solicitudes
en curso de la misma cuenta, las válidas pueden recibir 429. Además, su unidad
no se devuelve, porque el bloqueo ocurre antes de evaluar el código, y consume
cuota de la ventana. Es conservador (no permite eludir el límite), pero puede
bloquear de forma transitoria a un usuario legítimo con muchas solicitudes
simultáneas. No se propone cambio sin decisión de producto.

### 8.8. Regresiones (orquestador real)

| Ejecución | Resultado |
|---|---|
| Sin `-WithRedis`: `apps/{accounts,farms,inventario,permissions,trazabilidad}/tests` | 815 pruebas: 800 aprobadas, 0 fallidas, 0 errores y 15 omitidas (las 15 de los archivos nuevos que exigen Redis). Estado v1, solo el contenedor MariaDB, código 0. |
| Con `-WithRedis`: lo mismo salvo el archivo que detiene Redis | 814 pruebas: 813 aprobadas, **1 fallida** y 0 omitidas. Código 30, limpieza completa. |
| Pester del orquestador | 297 en total: 296 aprobadas, 0 fallidas, 1 omitida (symlink, requiere privilegio). |
| `tests/` (configuración y preflight), sin base | 402 aprobadas con `config.settings_test_disposable` y 82 aprobadas (`tests/test_settings_test.py`) con `config.settings_test` y credenciales ficticias. Ejecutar `test_settings_test.py` con la configuración desechable da 4 fallos esperables, porque la guarda toma la rama desechable. |

La fallida con Redis es
`test_invitation_code_accept_f1033b.py::test_window_is_fifteen_minutes`. Simula
el paso del tiempo parcheando el reloj de LocMemCache, que no afecta al TTL real
de Redis: es una limitación de esa prueba, no del producto. La expiración real
con Redis la cubren las pruebas E.

### 8.9. Discrepancia de hash concurrente

La guía registra `config/settings_test_disposable_isolated.py` como `2F4267F3…`
("BUILD F6"). El archivo vale `EED94C73…` desde antes de F10.3.4-B. Ninguna
etapa F10.3.4 lo ha modificado. Su contenido coincide con las migraciones
actuales de F6 y FARMS (`accounts.0006`, `farms.0005`, `trazabilidad.0008`), así
que el cambio es de la sesión concurrente. Se conservan ambos valores y no se
toca la fila de la guía. Lo mismo ocurre con `config/platform.py`, que pasó de
`E3F515EB…` a `F745A0F4…`.

## 9. Cierre F10.3: D-1, D-2 y D-3 corregidos (2026-11-12)

- **D-1 y D-3** (`apps/farms/code_attempts.py`, `apps/farms/atomic_cache.py`):
  - Cada ventana tiene un identificador aleatorio y contadores separados de
    fallos (`f`) e intentos en curso (`p`).
  - Cada reserva tiene un token. Reservar y resolver son scripts Lua atómicos en
    Redis; con LocMemCache se usa un cerrojo de proceso.
  - Un 404 convierte la reserva en fallo (`guard.failed()` en la vista); un 200
    o 409 la devuelve. Solo cuenta si la ventana y el token siguen vigentes, una
    sola vez y sin bajar de 0.
  - Una solicitud rechazada no consume unidad. Si solo la bloquean intentos en
    curso, espera hasta 2 s a que se libere un hueco.
- **D-2** (`apps/farms/throttles.py`):
  - `invitation_accept` (10/hora por cuenta) e `invitation_accept_ip` (30/hora
    por IP, solo `REMOTE_ADDR`) usan una ventana deslizante atómica: ZSET y
    script Lua. Los límites no cambian.
  - Se aplica a las tres vistas de aceptación: enlace, preview y accept.
- **Evidencia con Redis real y procesos independientes** (orquestador
  `-Validate -WithRedis`): 829 pruebas, todas aprobadas.
  - D-1: la reserva antigua deja la ventana nueva en (1, 0) y se evalúan 5
    fallos.
  - D-2: 3 ensayos de 15 procesos admiten exactamente 10; 32 procesos desde una
    IP admiten exactamente 30.
  - D-3: 10 procesos simultáneos dan 3×200, 3×409 y 4×404, y la ventana queda en
    (4, 0).
- **Con LocMemCache:** 830 pruebas, 814 aprobadas, 0 fallidas, 16 omitidas
  (las que exigen Redis real).
- **Redis detenido:** 500 en preview y accept, con la invitación `pending`.
- `test_window_is_fifteen_minutes` funciona ahora con LocMemCache (reloj
  simulado) y con Redis (TTL real y `PEXPIRE`).
