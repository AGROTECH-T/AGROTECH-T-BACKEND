# Orquestador desechable de validación — guía operativa

> Registro M3-2.4.4 (D04). Describe cómo usar el orquestador desechable que valida
> permisos, migraciones y pruebas sobre una MariaDB 11.8.8 efímera. Se basa en la
> inspección estática del código y en evidencia histórica de M3-2.4.3 y M3-2.4.4.
> **Esta guía no ha sido validada operativamente** y su redacción no ejecutó nada.
> Complementa [DEC-08](../decisions/DEC-08-fijacion-digest-mariadb-orquestador.md),
> [DEC-09](../decisions/DEC-09-alcance-mutex-orquestador-desechable.md) y
> [DEC-10](../decisions/DEC-10-riesgo-toctou-rutas-orquestador.md).

## Cómo leer esta guía

Cada afirmación indica su tipo de evidencia:

| Etiqueta | Significado |
|---|---|
| **[Código]** | Implementado y comprobado estáticamente en el código. |
| **[Pruebas]** | Cubierto por pruebas automatizadas ejecutadas en fases anteriores (Pester, pytest). |
| **[Integración]** | Observado en las ejecuciones reales de la integración M3-2.4.4 (2026-11-11). |
| **[Inferido]** | Deducido del comportamiento esperado de las herramientas; no demostrado. |
| **[No verificado]** | Diferido o sin comprobar. |
| **[Recomendación]** | Práctica operativa sugerida. **No es un control del código.** |

Versión del código descrita (SHA-256):

| Archivo | SHA-256 |
|---|---|
| `tools/run_permissions_disposable.ps1` | `73B8D6FC858E48B990327E6CCE87A6DBD9DE25797AA50265F4B058CF007F77F6` (F10.3.4-A: `-WithRedis` y `-RedisImageDigest`) |
| `tools/disposable_orchestrator_lib.ps1` | `4DB9E423451B066612C9F711E313B813CDCC34DF12E76ADE28D94DFE81365EBE` (F10.3.4-A: Redis opcional; sin `-WithRedis`, sin cambios de comportamiento) |
| `docker-compose.disposable.yml` | `24FD0F71F2A81B7407C971EB108C694998AABD585A09C2F2CBFB9817EC3F4382` |
| `docker-compose.disposable.redis.yml` | `0D48D7E851C429939E36746845822E6AA3182F666581767274DCCBADCFA847C0` (F10.3.4-A, solo con `-WithRedis`) |
| `config/disposable_preflight.py` | `AF200113B9D14337347B1FF48A67E07DA18AFF8172FC940788BC0BFB45238A3B` (F10.3.4-A: comprobación `redis`) |
| `config/settings_test_disposable.py` | `521E8BC67CB4488EEFA53666F8E4B251B89936FD4D5A00450A5644958B977538` (F10.3.4-A: caché Redis desechable opcional) |
| `config/settings_test_disposable_isolated.py` | `2F4267F37DAFACD449E24BA59A582E72FCFC55B9A9490A57987595DF2D3ADCE6` (BUILD F6: añade `accounts.0006` y `trazabilidad.0008` al grafo autorizado) |
| `conftest.py` | `6F0EE04FE174717A79EB75C9084AE52C1DF4528A5B0CEA747A71AD562A81FD42` |

Si alguno de estos archivos cambia, hay que revisar esta guía.

## 1. Propósito y arquitectura

El orquestador crea, para cada ejecución, un contenedor MariaDB 11.8.8 propio.
Antes de migrar o ejecutar pruebas, demuestra con la conexión real que Django está
conectado a ese contenedor. Al terminar, elimina solo los recursos cuya propiedad
puede demostrar. Nunca invoca `manage.py`.

| Capa | Componente | Responsabilidad |
|---|---|---|
| Punto de entrada | `tools/run_permissions_disposable.ps1` | Parámetros, ayuda (`Get-Help`), carga de la librería y código de salida final. |
| Lógica del orquestador | `tools/disposable_orchestrator_lib.ps1` | Validación de parámetros y rutas, mutex, procesos acotados (Job Object), comandos Docker, propiedad de recursos, estado e integridad, limpieza y recuperación. Al cargarse no ejecuta nada. |
| Infraestructura desechable | `docker-compose.disposable.yml` | Base de Compose: un servicio `mariadb` y una red. Todos los valores llegan como variables obligatorias. Puerto `127.0.0.1:3317:3306`, `tmpfs` en `/var/lib/mysql`, healthcheck y etiquetas base. |
| Infraestructura desechable | `<OutputDirectory>\compose\invocation.override.yml` (generado) | Fija la imagen por digest y añade las etiquetas `agrotech.invocation-id` y `agrotech.owner-proof`. Se escribe fuera del repositorio. |
| Validación Python | `config/disposable_preflight.py` | Subcomandos `preflight`, `migrate` y `sqlmigrate`; plugin de pytest que bloquea correo y HTTP. |
| Configuración de Django | `config/settings_test_disposable.py` | Destino desechable: nombres, host, puerto y usuario fijos; bases protegidas rechazadas al importar. |
| Configuración de Django | `config/settings_test_disposable_isolated.py` | Variante con `INSTALLED_APPS` limitado a accounts, farms y trazabilidad, y grafo de migraciones autorizado. |
| Guarda de pytest | `conftest.py` | Lista blanca de settings, `--reuse-db` obligatorio, `--create-db` prohibido y verificación en vivo antes de `django_db_setup`. |

## 2. Requisitos previos

| Requisito | ¿Lo comprueba el código? | Si falla |
|---|---|---|
| Windows y Windows PowerShell 5.1 (entorno de diseño) | No de forma explícita. **[Código]** usa DPAPI, named pipes y llamadas nativas de Windows. | — |
| Compilación del puente nativo (`Add-Type`) | **[Código]** Sin él no se ejecuta ningún proceso. Sin soporte nativo, `Get-FinalPathOrNull` no puede resolver la ruta física y `Test-RepositoryRootSafe` rechaza la raíz antes de cualquier otra comprobación. | 3 |
| DPAPI disponible | **[Código]** | 40 |
| Docker CLI en el `PATH` | **[Código]** | 40 |
| Docker Engine responde y Docker Compose está disponible | **[Código]** `docker version` y `docker compose version`. | 40 |
| Contexto Docker local | **[Código]** El endpoint debe ser `npipe:////./pipe/` seguido de `docker_engine`, `dockerDesktopLinuxEngine` o `dockerDesktopWindowsEngine`. Se rechazan `tcp://`, `ssh://` y pipes remotos. | 40 |
| Imagen `mariadb:11.8.8` en local con el digest de DEC-08 | **[Código]** `docker image ls --digests`. **No se descarga nunca** (`compose up --pull never`). | 10 |
| Puerto 3317 libre en la máquina | **[Código]** Cualquier listener TCP en ese puerto. | 50 |
| `.venv\Scripts\python.exe` | **[Código]** Solo comprueba que existe (ver DEC-10, T-5). | 40 |
| `docker-compose.disposable.yml` dentro de la raíz física del repositorio | **[Código]** | 40 |
| Raíz del repositorio sin symlinks ni junctions | **[Código]** | 3 |
| Rutas de salida y de estado fuera del repositorio | **[Código]** | 3 |
| Mutex libre (ninguna otra ejecución activa) | **[Código]** | 50 |
| Sin contenedores ni redes previos con los nombres o la etiqueta del RunId | **[Código]** | 50 |

Aclaraciones:

- El código comprueba que Docker y Compose responden, pero **no exige versiones
  mínimas**. Docker 29.9.2 y Compose 5.6.1 son las versiones de la integración
  histórica **[Integración]**, no requisitos.
- La imagen no se descarga automáticamente. Si falta en local, la ejecución termina
  con 10 sin crear nada.
- El código **no verifica las ACL** del directorio de salida ni del de estado
  **[No verificado]**.

## 3. Parámetros y modalidades

### 3.1. Parámetros

| Parámetro | Función | Restricciones (**[Código]**) | Valor por defecto |
|---|---|---|---|
| `-MariaDbImageDigest` | Fija la imagen como `mariadb:11.8.8@<digest>`. | Obligatorio en todos los modos salvo `-CleanupOnly`. Formato `sha256:` más 64 hexadecimales en minúscula. Es el digest del **índice OCI**, no el de una plataforma (DEC-08). | ninguno |
| `-Validate` | preflight, migrate, preflight posterior y pytest. | Incompatible con `-SqlMigrateOnly` y con `-CleanupOnly`. | desactivado |
| `-SqlMigrateOnly` | preflight y `sqlmigrate trazabilidad 0007`. | Fuerza `config.settings_test_disposable_isolated`; rechaza otro `-SettingsModule`. | desactivado |
| `-CleanupOnly` | Recuperación de una ejecución anterior. | Exige `-RunId` y `-StateFile`. Rechaza `-Validate`, `-SqlMigrateOnly`, `-Tests`, `-SettingsModule`, `-OutputDirectory` y `-MariaDbImageDigest`. | desactivado |
| `-RunId` | Identificador de la ejecución. | Exactamente 12 hexadecimales en minúscula. Obligatorio con `-CleanupOnly`; opcional en los demás modos (si se omite, se genera uno aleatorio). | generado |
| `-Tests` | Rutas para pytest. | Solo con `-Validate`. Rutas relativas existentes bajo `apps\…\tests`; los archivos deben llamarse `test_*.py`. Sin opciones (`-…`), comodines, `::`, `..`, rutas absolutas ni symlinks o junctions. | `apps/permissions/tests` |
| `-SettingsModule` | Configuración de Django del proceso hijo. | Solo `config.settings_test_disposable` o `config.settings_test_disposable_isolated`. | `config.settings_test_disposable` |
| `-OutputDirectory` | Resultados, logs, Compose y estado. | Absoluta, local (no UNC), fuera del repositorio y sin contenerlo. No puede ser la raíz de una unidad ni estar en ubicaciones del sistema, ni ser exactamente `USERPROFILE`, `LOCALAPPDATA`, `APPDATA` o `TEMP`. Sin symlinks ni junctions. Vacía o inexistente. | `%LOCALAPPDATA%\agrotech-disposable\runs\<RunId>-<8 primeros caracteres del InvocationId>` |
| `-StateFile` | `run-state.json` que se va a recuperar. | Solo con `-CleanupOnly`. Absoluta, existente, fuera del repositorio y sin symlinks ni junctions. | ninguno |
| `-HealthTimeoutSeconds` | Espera hasta `healthy`. | Entero de 10 a 600. | 120 |
| `-DockerCommandTimeoutSeconds` | Cada comando Docker. | Entero de 5 a 300. | 60 |
| `-ComposeTimeoutSeconds` | `compose config` y `up`. | Entero de 30 a 1800. | 300 |
| `-PreflightTimeoutSeconds` | Preflight. | Entero de 30 a 1800. | 300 |
| `-MigrateTimeoutSeconds` | migrate y sqlmigrate. | Entero de 30 a 3600. | 900 |
| `-PytestTimeoutSeconds` | pytest. | Entero de 60 a 14400. | 3600 |
| `-CleanupTimeoutSeconds` | Plazo global de limpieza. | Entero de 30 a 900. | 180 |

Los timeouts se admiten en todos los modos, también con `-CleanupOnly`. Cualquier
combinación distinta de las descritas termina con 3, sin crear recursos.

### 3.2. Ejecución normal (sin modo)

**[Código]**

1. Genera un RunId si no se indica.
2. Crea el contenedor y la red desechables.
3. Espera `healthy` y verifica identidad, puerto y privilegios del contenedor.
4. Ejecuta el preflight con el **esquema vacío** (`--expect-schema empty`).
5. Limpia.

**No ejecuta migraciones ni pruebas.** Mensaje final: "Preflight correcto; entorno
eliminado. No se ejecutaron migraciones ni pruebas."

### 3.3. `-Validate`

**[Código]** Tras crear el entorno:

1. Subcomando `migrate`: ejecuta el preflight y, solo si da OK, la migración.
2. Preflight posterior con `--expect-schema migrated`: no deben quedar migraciones
   pendientes ni tablas inesperadas.
3. pytest con las rutas de `-Tests`.

Genera los informes `preflight-*.json`, `mailguard-pytest.json` y `junit.xml` en
`OutputDirectory`.

> **Advertencia:** pytest-django puede volver a ejecutar `migrate` dentro de pytest.
> Lee la sección 4 antes de usar este modo.

Solo `apps/permissions/tests` tiene evidencia de ejecución **[Integración]**: 72
pruebas aprobadas en cada una de tres ejecuciones.

### 3.4. `-SqlMigrateOnly`

**[Código]**

- Fuerza `config.settings_test_disposable_isolated`.
- Ejecuta el preflight y, solo si da OK, `sqlmigrate trazabilidad 0007`.
- **Solo genera el SQL de referencia; no aplica migraciones ni modifica el esquema.**
- La salida queda en `logs\NNN-python-sqlmigrate.log`.

### 3.5. `-CleanupOnly`

**[Código]** Recupera una ejecución anterior a partir de su `-RunId` y su
`-StateFile`:

1. Verifica que el estado corresponde al RunId y que es auténtico: HMAC-SHA256 y
   DPAPI del mismo usuario de Windows.
2. Vuelve a validar contra Docker la identidad y la propiedad de cada recurso.
3. Elimina solo lo que es propio.

**No debe usarse para limpiar recursos que no pertenezcan a esa ejecución**: el
código lo rechaza con 50. Los detalles están en la sección 7.

### 3.6. `-Validate -WithRedis` (F10.3.4-A)

**[Código]** Igual que `-Validate`, pero añade un contenedor `redis:7.4.12`
propio de la ejecución, en `127.0.0.1:16379`, como caché de Django durante
pytest. Exige `-RedisImageDigest`. Sin `-WithRedis`, nada de esta guía cambia.
El diseño, los controles, el estado de integridad v2 y las limitaciones están en
[orquestador-desechable-redis.md](orquestador-desechable-redis.md).
**[Integración]** Ejecutado con contenedores reales en F10.3.4-B. El digest
aprobado está en
[DEC-12](../decisions/DEC-12-fijacion-digest-redis-orquestador.md) y los
resultados, en la sección 8 de ese documento.

## 4. Migraciones y pytest: advertencia obligatoria

Ruta de migración en `-Validate`:

1. **[Código]** El subcomando `migrate` de `config/disposable_preflight.py` exige un
   preflight correcto con el esquema **vacío**.
2. **[Código]** Ejecuta `call_command("migrate", interactive=False)`.
3. **[Código]** Repite el preflight para verificar el esquema migrado (sin
   migraciones pendientes). Si falla, el código es 20.
4. **[Código]** Antes de pytest, el orquestador ejecuta además
   `preflight --expect-schema migrated`. Si este preflight rechaza, el código es
   10, **aunque las migraciones ya se hayan aplicado** en la base desechable.
5. **[Código]** El orquestador inicia pytest con estos argumentos fijos:

   ```
   python.exe -B -E -s -X utf8 -m pytest -p config.disposable_preflight -p no:cacheprovider -o addopts= --ds=<SettingsModule> --reuse-db --junitxml=<OutputDirectory>\junit.xml <Tests…>
   ```

6. **[Inferido]** pytest-django puede invocar internamente `migrate` con
   `keepdb=True` al preparar la base (`django_db_setup`).

> **Advertencia: la segunda llamada a `migrate` es una inferencia técnica.**
>
> - No se observó en ninguna ejecución: los registros históricos no la muestran.
> - No se verificó leyendo el código interno de pytest-django o Django instalado.
> - Se espera que **no modifique el esquema**, porque las migraciones ya están
>   aplicadas y verificadas por el preflight posterior.
> - Esa llamada **no repite** la validación completa del preflight (privilegios y
>   versión del servidor).
> - La protegen tres barreras:
>   1. Los settings desechables, que fijan el destino al importarse.
>   2. `conftest.py`, que exige `--reuse-db`, prohíbe `--create-db` y verifica en
>      solo lectura base, usuario, puerto interno, hostname del contenedor, InnoDB
>      y existencia del esquema antes de `django_db_setup`.
>   3. El preflight posterior a la migración.
> - **No es una garantía absoluta demostrada.**

- **[Código]** `--reuse-db` lo fija el orquestador. `-Tests` no admite opciones, por
  lo que no es posible sustituirlo por `--create-db` ni eliminarlo. El entorno hijo
  no hereda `PYTEST_*` ni `PYTHON*`, y `-o addopts=` anula el `addopts` de
  `pyproject.toml`.
- **[Recomendación]** **No ejecutes pytest manualmente con
  `config.settings_test_disposable` ni `config.settings_test_disposable_isolated`
  fuera del orquestador.** La confirmación, el RunId y la contraseña de la base
  desechable solo los aporta el orquestador.
- **[No verificado]** Las rutas de `-Tests` distintas de `apps/permissions/tests` no
  se revisaron por completo en busca de usos explícitos de
  `django_db_blocker.unblock()` o `call_command("migrate")`. Cualquier conexión
  seguiría limitada al destino desechable por los settings.

## 5. Seguridad de bases de datos

| Barrera | Evidencia |
|---|---|
| RunId de 12 hexadecimales en minúscula. | **[Código]** **[Pruebas]** |
| Esquema `agrotech_permtest_<RunId>`; `TEST.NAME` igual a `NAME`. | **[Código]** **[Pruebas]** |
| Usuario `agrotech_perm_validation`, host `127.0.0.1` y puerto publicado `3317`, únicos valores aceptados. | **[Código]** **[Pruebas]** |
| Bases protegidas rechazadas por nombre: `agrotech_db`, `agrotech_t`, `agrotech_farms_test` y `agrotech_farms_validation`. | **[Código]** **[Pruebas]** |
| Identidad real del servidor: `DATABASE()`, `CURRENT_USER()`, puerto interno 3306, hostname `agrotech-permtest-<RunId>`, MariaDB 11.8.8 e InnoDB. | **[Código]** **[Pruebas]** **[Integración]** |
| Privilegios mediante `SHOW GRANTS`: sin roles, sin GRANT OPTION ni PROXY, solo `USAGE` global y privilegios únicamente sobre el esquema de la ejecución. | **[Código]** **[Pruebas]** |
| Acceso del usuario desechable a `agrotech_db` y `agrotech_t` denegado (ERROR 1044). | **[Integración]** |
| Bloqueo de correo y HTTP: SMTP y `requests` sustituidos por un bloqueo; backend `locmem`; variables SMTP y Brevo vacías; `.env` desactivado. | **[Código]** **[Pruebas]** **[Integración]** (0 intentos registrados) |
| Entorno hijo construido desde una lista blanca: sin `DOCKER_*`, `COMPOSE_*`, `PYTEST_*`, `PYTHON*`, `DJANGO_*` ni credenciales heredadas. | **[Código]** **[Pruebas]** |
| Credenciales: contraseñas aleatorias por ejecución; redactadas en los logs; la salida de `SHOW GRANTS` y de las inspecciones no se registra (el log muestra `<omitido>`); los informes no incluyen contraseñas. | **[Código]** **[Pruebas]** **[Integración]** |

Estas barreras **no constituyen un aislamiento absoluto demostrado**. Ver la
sección 9.

## 6. Recursos y aislamiento

| Recurso | Valor |
|---|---|
| Proyecto Compose, contenedor y hostname | `agrotech-permtest-<RunId>` |
| Red | `agrotech-permtest-<RunId>-net` |
| Esquema | `agrotech_permtest_<RunId>` |
| Usuario | `agrotech_perm_validation` |
| Puerto | `127.0.0.1:3317` → `3306` del contenedor |
| Datos | `tmpfs` en `/var/lib/mysql`; sin volúmenes declarados |
| Etiquetas base (Compose) | `agrotech.disposable=true`, `agrotech.run-id=<RunId>`, `agrotech.purpose=permissions-validation` |
| Etiquetas de la invocación (override) | `agrotech.invocation-id=<InvocationId>` (32 hexadecimales, uno por ejecución) y `agrotech.owner-proof` (SHA-256 de un token aleatorio de 256 bits por ejecución) |
| Archivos en `OutputDirectory` | `run-state.json` (y su `.tmp`), `logs\NNN-*.log`, `compose\invocation.override.yml`, `compose\empty.env`, `preflight-*.json`, `junit.xml`, `mailguard-pytest.json`; en la recuperación, `cleanup-<hex>\` |

Propiedad **[Código]**: un recurso solo es propio si coinciden su nombre exacto, las
tres etiquetas base, el `invocation-id`, el `owner-proof` y, en el contenedor,
también el hostname.

- Cualquier otro resultado (ajeno o ambiguo) impide el borrado.
- Antes de borrar se vuelven a validar la propiedad y el ID. El borrado es siempre
  por ID: contenedor con `container rm -f <id>` (sin `-v`) y red con
  `network rm <id>`.
- Un volumen anónimo solo se borra si estaba montado en el contenedor propio, ya no
  lo usa nadie y está marcado como anónimo o del proyecto propio. Un volumen
  nombrado nunca se borra.
- Ningún error de Docker se interpreta como "recurso ausente".

**[Recomendación]** No uses `docker system prune`, `docker volume prune`,
`docker compose down -v`, `docker rm -v` ni borrados globales.

## 7. Estado, recuperación y limpieza

### 7.1. `run-state.json`

**[Código]** Se escribe en cada cambio de estado y contiene:

- RunId, InvocationId, modo, SettingsModule, Tests y contexto Docker;
- los nombres de proyecto, contenedor, red y base;
- `Owned` (ContainerId, NetworkId, Volumes);
- `State`, `StageCode`, `StageMessage`, `Cleanup`, `ExitCode`, `History` (estado y
  hora de cada transición) y `TerminationFailures`;
- `Integrity`.

Estados que aparecen en el historial:

- **Intermedios:** `NOT_STARTED`, `ENVIRONMENT_CREATED`, `PREFLIGHT_PASSED`,
  `MIGRATIONS_COMPLETED`, `TESTS_COMPLETED`, `FAILED` y `ABORTED_PREEXISTING`.
- **Finales:**
  - `NO_RESOURCES_CREATED`: no se creó nada.
  - `CLEANUP_COMPLETED`: había recursos y se eliminaron todos.
  - `CLEANUP_INCOMPLETE`: se agotó el plazo de limpieza.
  - `CLEANUP_FAILED`: una operación falló o se negó a borrar.
  - `INTERRUPTED`: la ejecución se interrumpió.
- **Recuperación** (`-CleanupOnly`, con un bloque `Recovery`): `RECOVERY_COMPLETED`,
  `RECOVERY_INCOMPLETE`, `RECOVERY_FAILED` y `RECOVERY_REFUSED`.

### 7.2. Integridad

- **[Código]** El token de cada ejecución se guarda solo cifrado con **DPAPI
  `CurrentUser`**, con el InvocationId como entropía.
- **[Código]** Un **HMAC-SHA256** con ese token protege los identificadores, los
  nombres, los IDs de recursos y los volúmenes.
- **[Código]** Un estado alterado o sin bloque `Integrity` se rechaza con 50.
- **[Inferido]** **Solo el mismo usuario de Windows** que creó el entorno puede
  recuperarlo: para otro usuario, DPAPI no puede descifrar el token y el código
  toma la misma rama de rechazo (50). Se deduce del código; **no se ha probado
  con otro usuario real de Windows**. Las pruebas históricas cubren el rechazo
  por InvocationId incompatible **[Pruebas]**.
- **[Integración]** Un estado alterado devolvió 50 sin borrar nada.
- El estado nunca autoriza un borrado por sí solo: cada recurso se vuelve a validar
  contra Docker.

### 7.3. Interrupciones

- **[Código]** Si la ejecución se interrumpe (por ejemplo, con Ctrl+C), el
  orquestador intenta limpiar lo propio, persiste el estado `INTERRUPTED` y
  devuelve 43.
- La ayuda del script advierte que **el código observado puede no ser 43**. La
  referencia es `run-state.json`.

### 7.4. Recuperación con `-CleanupOnly`

Cuando el código es 41, 42 o 43, usa la línea que imprime la consola al inicio:
`Recuperación: -CleanupOnly -RunId <RunId> -StateFile "<ruta>\run-state.json"`.

Resultados **[Código]**:

Validación del StateFile y del entorno de recuperación (no se toca ningún
recurso):

| Situación | Código |
|---|---|
| Ruta relativa, dentro del repositorio, inexistente, con symlink o junction, o físicamente interna | 3 |
| Archivo no legible o JSON inválido | 3 |
| RunId o InvocationId incompatibles | 50 |
| Bloque de integridad ausente o con versión incorrecta | 50 |
| Nombres incoherentes con el RunId, DPAPI no puede descifrar el token o HMAC incorrecto | 50 |
| El directorio de estado o el del registro de limpieza deja de ser válido al utilizarlo | 3 |
| Docker CLI no encontrado | 40 |
| Mutex ocupado | 50 |

**[Inferido]** Un StateFile vacío terminaría con 50 según la inspección del
código; no está demostrado.

Resultado de la recuperación de recursos:

| Situación | Estado | Código |
|---|---|---|
| Recursos propios eliminados (`Removed`) o ya inexistentes (`AlreadyAbsent`) | `RECOVERY_COMPLETED` | 0 |
| Recurso ajeno o ambiguo | `RECOVERY_REFUSED` | 50 |
| Una operación de borrado falla o la terminación no se confirma | `RECOVERY_FAILED` | 41 |
| Se agota el plazo de limpieza | `RECOVERY_INCOMPLETE` | 41 |
| Contexto Docker no local | `RECOVERY_FAILED` | 40 |
| No se puede reescribir el estado al final | — | 40 cuando el resultado previo era 0; si no, se conserva el código previo |

`AlreadyAbsent` es un resultado válido: el recurso propio ya no existe. **No es
un fallo de limpieza.**

**[Recomendación]** Si la recuperación devuelve 50, no borres a mano. Antes,
comprueba las etiquetas `agrotech.invocation-id` y `agrotech.owner-proof` del
recurso con `docker inspect`, sin volcar su configuración de entorno.

### 7.5. Comprobación posterior a la limpieza

**[Recomendación]** Revisa estos puntos:

1. Código de salida 0 y mensaje final del modo.
2. En `run-state.json`, `State` es `CLEANUP_COMPLETED`, o `NO_RESOURCES_CREATED` si
   no llegó a crearse nada.
3. `Cleanup.Ok` es `true`, `Cleanup.Problems` está vacío y `Container` y `Network`
   valen `Removed`, `AlreadyAbsent` (el recurso propio ya no existía) o
   `NotOwned` (no se crearon).
4. En una recuperación, el bloque `Recovery` tiene `State = RECOVERY_COMPLETED` y
   `ExitCode = 0`.
5. `TerminationFailures` está vacío.
6. `docker ps -a --filter "name=agrotech-permtest-<RunId>"` y
   `docker network ls --filter "name=agrotech-permtest-<RunId>"` no devuelven
   nada.
7. No eliminas recursos cuya propiedad no hayas verificado.

## 8. Códigos de salida

| Código | Significado | Condiciones principales (**[Código]**) |
|---|---|---|
| 0 | OK | Modo completado y limpieza correcta, o nada que limpiar. |
| 1 | Error de PowerShell | **No pertenece a la tabla del orquestador.** Lo produce PowerShell ante un error de enlace de parámetros (nombre o tipo inexistente) o una excepción no capturada. No es una conversión garantizada del orquestador. |
| 3 | PARAMETERS_INVALID | Parámetros o combinación no permitidos; raíz insegura, incluida la falta de soporte nativo (no se puede resolver la ruta física); rutas no permitidas, también en el momento de uso; StateFile en ruta no permitida, ilegible o con JSON inválido (sección 7.4). No se crean recursos. |
| 10 | PREFLIGHT_REJECTED | Rechazos del preflight, y también otros rechazos de la etapa: directorio de Compose con archivos no autorizados, imagen fijada no disponible, configuración de Compose renderizada inválida, identidad, puerto o privilegios del contenedor. **No equivale solo a "preflight fallido".** Un rechazo del preflight inicial ocurre antes de migrar; un rechazo del preflight posterior (`--expect-schema migrated`) ocurre **después** de aplicar las migraciones. **El código 10 no permite deducir por sí solo si la migración llegó a ejecutarse**: revisa `History` en `run-state.json` (`MIGRATIONS_COMPLETED`). No se ejecutan pruebas. |
| 20 | MIGRATION_FAILED | Lo devuelve el subcomando `migrate` o `sqlmigrate` de `config/disposable_preflight.py` y el orquestador lo convierte: excepción durante la operación o, en `migrate`, la verificación interna posterior a la migración no superada. |
| 30 | TESTS_FAILED | pytest terminó con un código distinto de 0. **[Inferido]** Un bloqueo de la guarda de `conftest.py` también produce un código distinto de 0 y, por tanto, 30. |
| 40 | INFRASTRUCTURE_FAILED | DPAPI no disponible; un proceso Python hijo (preflight, `migrate` o `sqlmigrate`) devuelve un código distinto de 0, 10 o 20 y `ConvertTo-StageCode` lo convierte en 40; Docker, Compose, `.venv` o el archivo Compose no disponibles; contexto no local; `compose up` incompleto (incluida una colisión del puerto durante `up`); el contenedor no llega a `healthy`; timeout; terminación de procesos no confirmada; error interno; estado final no persistido. |
| 41 | CLEANUP_FAILED | La etapa terminó bien, pero la limpieza falló o quedó incompleta. En recuperación: fallo, plazo agotado o terminación no confirmada. Recupera con `-CleanupOnly`. |
| 42 | STAGE_AND_CLEANUP_FAILED | Fallaron la etapa y la limpieza (`Get-FinalExitCode`: etapa distinta de 0 y limpieza requerida no correcta). Ejemplo: propiedad no demostrada tras `up` por un volumen nombrado montado (ver 50). Recupera con `-CleanupOnly`. |
| 43 | INTERRUPTED | Ejecución interrumpida; revisa `run-state.json`. |
| 50 | PREEXISTING_OR_OWNERSHIP | Recursos previos del RunId; puerto 3317 ocupado en la comprobación previa; mutex ocupado o inaccesible; StateFile que no corresponde o no es auténtico (sección 7.4); recuperación rechazada. Con propiedad no demostrada tras `up`, la **etapa** queda con 50; si después la limpieza también falla por esa condición (por ejemplo, un volumen nombrado montado que nunca se borra), el código **final** es 42 (prueba Pester histórica, `run_permissions_disposable.Tests.ps1`, línea 1940). No se elimina nada ajeno. |

## 9. Riesgos, decisiones y limitaciones

- **[DEC-08](../decisions/DEC-08-fijacion-digest-mariadb-orquestador.md), digest de
  MariaDB.** Se usa el digest del índice OCI de `mariadb:11.8.8`, verificado el
  2026-11-11. Si la etiqueta se vuelve a publicar, hay que verificarlo de nuevo y
  actualizar DEC-08.
- **[DEC-09](../decisions/DEC-09-alcance-mutex-orquestador-desechable.md), mutex.**
  `Global\AgrotechDisposableOrchestrator`.
  - Exclusión entre procesos de la **misma sesión**: verificada **[Pruebas]**.
  - Entre **sesiones o usuarios distintos**: **no demostrada** **[No verificado]**.
  - Si el puerto 3317 ya está ocupado en la comprobación previa, el resultado es
    50.
  - Si otra ejecución lo toma entre la comprobación y `compose up`, la ejecución
    perdedora devuelve **40** y limpia solo lo suyo.
- **[DEC-10](../decisions/DEC-10-riesgo-toctou-rutas-orquestador.md), riesgo TOCTOU
  residual.** Aceptado.
  - La revalidación de rutas es parcial: una vez por directorio y no antes de cada
    escritura.
  - El orquestador no borra archivos, pero **puede sobrescribir** archivos con
    nombres fijos en una ubicación redirigida.
  - **No se ha reproducido ninguna explotación real.**

Otras limitaciones:

- **[No verificado]** El rechazo de **symlinks** no se ha probado por falta de
  privilegios; sí se probaron las junctions **[Pruebas]**.
- **[No verificado]** No se comprueban **ACL** específicas de `OutputDirectory` ni
  del directorio de estado. **[Recomendación]** Usa una carpeta propia del usuario
  donde no escriban otros.
- **[No verificado]** Las rutas de **`-Tests`** distintas de
  `apps/permissions/tests` no se revisaron por completo.
- **[Código]** La ruta de pytest **no repite** la validación de privilegios ni de
  versión del servidor; se apoya en el preflight previo.
- **[Inferido]** La segunda llamada a `migrate` dentro de pytest (sección 4).
- **[Código]** `.venv\Scripts\python.exe` solo se comprueba con `Test-Path` (DEC-10,
  T-5).
- **No se ha demostrado aislamiento absoluto.** El motor Docker y la máquina se
  comparten con otros contenedores.

Las mejoras posibles descritas en DEC-09 y DEC-10 son **propuestas futuras, no
funcionalidades implementadas**.

## 10. Procedimiento operativo

Los marcadores de posición van entre `<…>`. El digest oficial (DEC-08) es el de
`mariadb:11.8.8@sha256:24e76fcec8c003a0362d0dd53f4806e7e79458d7fdeaf47437760e19496f5a9c`,
es decir, `-MariaDbImageDigest sha256:24e76fcec8c003a0362d0dd53f4806e7e79458d7fdeaf47437760e19496f5a9c`.

### Paso 1. Comprobaciones previas [Recomendación]

1. Docker Desktop está en marcha y el contexto es local (`docker context show`).
2. La imagen fijada está en local. Este comando debe mostrar el digest anterior:

   ```powershell
   docker image ls --no-trunc --digests --filter reference=mariadb:11.8.8
   ```

3. El puerto 3317 está libre.
4. Existe `.venv\Scripts\python.exe`.
5. No hay otra ejecución del orquestador en curso.
6. Si vas a indicar `-OutputDirectory`, la carpeta está fuera del repositorio, es
   propia del usuario y está vacía o no existe.

### Paso 2. Selección de modalidad

| Objetivo | Modalidad |
|---|---|
| Comprobar que el entorno se crea y el preflight pasa | Ejecución normal |
| Migrar y ejecutar pruebas | `-Validate` (lee la sección 4) |
| Obtener el SQL de `trazabilidad 0007` sin aplicarlo | `-SqlMigrateOnly` |
| Recuperar una ejecución con código 41, 42 o 43 | `-CleanupOnly` |

### Paso 3. Ejecución en una sola sesión de PowerShell

Ejecuta desde la raíz del repositorio y en una única sesión.
**[Recomendación]** Las ejecuciones históricas usaron
`powershell -NoProfile -ExecutionPolicy Bypass -File …`; el código no lo exige.

Comandos del script, con los parámetros reales:

```powershell
# Ejecución normal
.\tools\run_permissions_disposable.ps1 -MariaDbImageDigest sha256:24e76fcec8c003a0362d0dd53f4806e7e79458d7fdeaf47437760e19496f5a9c

# Validación completa con las pruebas por defecto y una carpeta de resultados propia
.\tools\run_permissions_disposable.ps1 -Validate -MariaDbImageDigest sha256:24e76fcec8c003a0362d0dd53f4806e7e79458d7fdeaf47437760e19496f5a9c -OutputDirectory <ruta-absoluta-fuera-del-repositorio>

# Validación con rutas de pruebas concretas (ver advertencia de -Tests en la sección 4)
.\tools\run_permissions_disposable.ps1 -Validate -Tests <apps/…/tests> -MariaDbImageDigest sha256:24e76fcec8c003a0362d0dd53f4806e7e79458d7fdeaf47437760e19496f5a9c

# Generación del SQL de trazabilidad 0007 (la configuración aislada se aplica sola)
.\tools\run_permissions_disposable.ps1 -SqlMigrateOnly -MariaDbImageDigest sha256:24e76fcec8c003a0362d0dd53f4806e7e79458d7fdeaf47437760e19496f5a9c
```

Al inicio, la consola muestra el RunId, el InvocationId, los nombres de los
recursos, la carpeta de resultados y la línea de recuperación. Anótalos.

### Paso 4. Revisión de logs e informes [Recomendación]

1. Código de salida y mensaje final (sección 8).
2. `run-state.json`: `State`, `ExitCode`, `StageMessage`, `Cleanup` e `History`.
3. `preflight-*.json`: `status = OK` y todas las comprobaciones con `ok = true`.
4. `mailguard-pytest.json` (en `-Validate`): `smtp_attempts` y `http_attempts` a 0.
5. `junit.xml` (en `-Validate`): pruebas, fallos y errores.
6. `logs\`: un archivo por comando, con la salida redactada. En los comandos
   cuya salida puede contener datos sensibles (por ejemplo, `SHOW GRANTS` e
   inspecciones), el log muestra `<omitido>` en lugar de la salida. No supongas
   que todos los valores sensibles aparecen redactados literalmente.
7. Si el código es 10 en `-Validate`, comprueba en `History` si aparece
   `MIGRATIONS_COMPLETED`: en ese caso las migraciones sí se aplicaron.

### Paso 5. Verificación de la limpieza

Aplica la lista de la sección 7.5.

### Paso 6. Recuperación cuando corresponda

Con un código 41, 42 o 43, o con un estado que no sea `CLEANUP_COMPLETED` ni
`NO_RESOURCES_CREATED`:

```powershell
.\tools\run_permissions_disposable.ps1 -CleanupOnly -RunId <RunId-de-la-ejecución> -StateFile "<OutputDirectory-de-la-ejecución>\run-state.json"
```

Después, revisa el bloque `Recovery` y repite la sección 7.5. Si devuelve 50,
consulta la sección 7.4.

### Paso 7. Cierre y conservación de evidencias [Recomendación]

1. Conserva la carpeta de resultados (`run-state.json`, `logs\`, informes y
   `junit.xml`) fuera del repositorio.
2. Registra el código de salida, el RunId y la versión del código (hashes del
   inicio de esta guía).
3. No copies al repositorio logs ni estados de ejecución.
