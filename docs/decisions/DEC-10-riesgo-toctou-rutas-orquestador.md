# DEC-10 — Riesgo TOCTOU residual en rutas del orquestador desechable (AUD-013)

- **Identificador:** DEC-10
- **Título:** Riesgo TOCTOU residual en rutas del orquestador desechable (AUD-013)
- **Estado de decisión:** Aprobada
- **Estado de implementación:** Parcial
- **Alcance:** M3-2.4.4 / orquestador desechable
- **Fecha:** 2026-11-11
- **Relación:** AUD-013
- **Tipo:** aceptación y documentación del riesgo residual

## Contexto

AUD-013 identifica un riesgo TOCTOU (*time-of-check to time-of-use*): una ruta o
un directorio validado puede sustituirse, por ejemplo por una junction, antes de
que el orquestador lo utilice.

La revisión estática de M3-2.4.4 sobre `tools/disposable_orchestrator_lib.ps1`
(versión con SHA-256 `CA92A148A1664341442C6F70C6EB95B33D6A99AC4243A9C9766207675C8BBF17`)
confirmó tres controles:

- validación de rutas léxicas y físicas;
- detección de puntos de reparación (*reparse points*);
- revalidación parcial antes del uso.

Esos controles no eliminan todas las ventanas TOCTOU, por cuatro motivos:

- la revalidación se realiza una vez por directorio, no antes de cada escritura;
- la creación del directorio de salida ocurre antes de su revalidación;
- algunas rutas del repositorio se validan una sola vez;
- las escrituras posteriores usan rutas, no handles de directorio retenidos.

## Decisión

- El usuario acepta registrar el riesgo residual descrito en este documento
  **sin implementar correcciones en M3-2.4.4**.
- Se conservan todas las validaciones existentes. No se debilita ninguna ni se
  aceptan redirecciones no verificadas.
- No se ejecutan pruebas de carreras en esta fase.

## Controles técnicos revisados

Las comprobaciones **léxicas** y **físicas** son controles diferentes:

- La ruta **léxica** es la cadena normalizada con `GetFullPath`, sin consultar
  el sistema de archivos. `Test-IsPathWithin` compara solo prefijos de texto.
- La ruta **física** es la que devuelve el sistema tras seguir junctions y
  symlinks.

Una ruta léxicamente externa puede ser físicamente interna, y al revés.
Por eso `Assert-DirectoryInUse` y `Test-RepositoryRootSafe` exigen que la ruta
física coincida con la léxica.

### C-4 — `Get-FinalPathOrNull` y `FinalPath`

- **Ubicación:** `Get-FinalPathOrNull` en las líneas 170-182; método nativo
  `FinalPath` en las líneas 933-945; declaraciones `CreateFileW` y
  `GetFinalPathNameByHandleW` en las líneas 814-817.
- **Comportamiento:**
  - Si la ruta no existe o no hay soporte nativo, devuelve `$null`.
  - Abre el elemento con `CreateFileW`: `FILE_READ_ATTRIBUTES`, compartiendo
    lectura, escritura y borrado, `OPEN_EXISTING` y
    `FILE_FLAG_BACKUP_SEMANTICS`. No usa `FILE_FLAG_OPEN_REPARSE_POINT`, así que
    **el sistema sigue junctions y symlinks**.
  - Obtiene la ruta final con `GetFinalPathNameByHandleW` y le quita el prefijo
    `\\?\` o `\\?\UNC\`.
  - Ante un handle inválido o una longitud no válida, devuelve `null`.
- **El handle se cierra al terminar la consulta.** No se conserva para las
  escrituras posteriores.

### C-5 — `Get-NearestExistingFinalPath` y `Test-PathPhysicallyWithin`

- **Ubicación:** líneas 184-203 y 225-232.
- **Comportamiento:**
  - Resuelve la ruta física del ancestro existente más cercano y le añade la
    parte que todavía no existe.
  - Compara la contención física con `Test-IsPathWithin`.
- **Resultado:** `$true`, `$false` o `$null` si no puede resolver. Las funciones
  que lo consumen tratan el resultado indeterminado como rechazo: `-ne $false`
  en las líneas 248, 353 y 563, y `-ne $true` en las líneas 291 y 2649.

### C-8 — `Resolve-AuthorizedTestPath` (`-Tests`)

- **Ubicación:** líneas 251-304. Se invoca desde `Test-OrchestratorOptions`
  (línea 496).
- **Controles, en orden:**
  1. Elemento no vacío.
  2. Sin opciones de pytest.
  3. Sin comodines, identificadores de nodo (`::`) ni NUL.
  4. Sin segmentos `..`.
  5. Ruta relativa.
  6. Ruta léxica bajo `<Root>\apps`.
  7. La ruta existe.
  8. Sin reparse points desde la raíz.
  9. Contención física dentro de `apps`.
  10. Algún segmento se llama `tests`.
  11. Si es un archivo, se llama `test_*.py`.
- Devuelve la ruta relativa.
- **No existe revalidación antes de que pytest utilice esas rutas**
  (`Get-PytestArguments`, línea 2075, con directorio de trabajo `Root`, línea
  2053).

### C-9 — `Resolve-SafeOutputDirectory` (`OutputDirectory`)

- **Ubicación:** líneas 306-368. Se invoca en la línea 507 (parámetro del
  operador) o en la línea 2603 (ruta por defecto).
- **Controles, en orden:**
  1. Ruta no vacía.
  2. Absoluta y no UNC.
  3. No está dentro del repositorio ni lo contiene.
  4. No es la raíz de una unidad.
  5. No está en ubicaciones protegidas del sistema.
  6. No es exactamente una carpeta compartida del perfil.
  7. Sin reparse points.
  8. Contención física fuera del repositorio.
  9. Si existe, es un directorio vacío.
- **Uso:** el directorio se crea con `New-Item -Force` (línea 2605) y **después**
  se revalida con `Assert-DirectoryInUse` (línea 2607).
- **Controles que repite `Assert-DirectoryInUse`** (líneas 234-249): existencia
  y tipo, ausencia de reparse points, igualdad entre ruta física y léxica, y
  contención física fuera del repositorio.
- **Controles que no se repiten:**
  - Las propiedades léxicas de la cadena (pasos 2 a 6). La cadena no cambia, y
    cualquier redirección física la detectan los controles repetidos. Esto se
    infiere por inspección.
  - Que el directorio esté vacío. Esta propiedad sí puede cambiar y no se
    revalida.

## Mitigaciones y limitaciones

Mitigaciones existentes. Ninguna constituye una protección completa:

- **Detección de reparse points:** `Get-ReparsePointInChain` (líneas 131-168),
  aplicada a la raíz, `-Tests`, `OutputDirectory`, `StateFile`, el archivo
  Compose y las revalidaciones.
- **Comparación de rutas físicas y léxicas:** C-4, C-5,
  `Test-RepositoryRootSafe` (líneas 205-223) y `Assert-DirectoryInUse`.
- **Revalidación parcial con `Assert-DirectoryInUse`:**
  - Directorio de salida, `logs` y `compose`: líneas 2607 y 2617-2618.
  - Directorio del estado y log de limpieza en `-CleanupOnly`: líneas 2432 y
    2444.
- **Validación del archivo Compose y del directorio Compose:**
  - Archivo: comprobación de la línea 2649 (archivo, sin reparse points desde la
    raíz, físicamente dentro de la raíz).
  - Directorio: `Test-ComposeDirectoryClean` (líneas 1815-1830), que revalida el
    directorio y exige exactamente `empty.env` (0 bytes) e
    `invocation.override.yml`.
- **Validación de `StateFile`:** `Resolve-StateFilePath` (líneas 545-567) en la
  línea 517 y de nuevo justo antes de reescribirlo (línea 2522).
- **Sin borrados de archivos:** no existen operaciones de eliminación de
  archivos en las rutas inspeccionadas (no hay `Remove-Item` en la librería).

> La revalidación está implementada de forma parcial: se realiza una vez por directorio y no antes de cada escritura. Además, la creación del directorio de salida es anterior a su revalidación.

No borrar archivos no significa que no pueda haber sobrescrituras.
`Write-Utf8File` usa `WriteAllText` y el estado se sustituye con
`Move-Item -Force`. Por tanto, el orquestador puede sobrescribir archivos con
nombres fijos, entre ellos:

- `run-state.json` y `run-state.json.tmp`;
- los logs `logs\NNN-*.log`;
- `invocation.override.yml` y `empty.env`;
- `junit.xml`;
- `preflight-*.json` y `mailguard-pytest.json`.

El contenido escrito está saneado: las contraseñas se redactan, el token se
guarda cifrado con DPAPI y la etiqueta `owner-proof` es un hash del token.

## Riesgos residuales T-1 a T-7

**Condición común a todos.** El atacante necesita permiso de escritura sobre el
directorio padre de la ruta afectada y actuar durante la ejecución.

- Para la ruta de salida por defecto
  (`%LOCALAPPDATA%\agrotech-disposable\runs\…`) se **infiere**, sin haberlo
  comprobado, que ese permiso corresponde al mismo usuario, a administradores o
  a SYSTEM.
- Para las rutas del repositorio, el atacante necesita permiso de escritura
  sobre el repositorio.

Ningún riesgo de esta sección ha sido explotado ni reproducido.

### Riesgos principales de AUD-013

#### T-1 — Entre la validación de `OutputDirectory` y `New-Item -Force`

- **Ventana:** desde `Resolve-SafeOutputDirectory` (línea 507 o 2603) hasta
  `New-Item -Force` (línea 2605).
- **Recurso:** el directorio de salida y su cadena de directorios padre.
- **Efecto técnicamente posible:** si un componente se sustituye por una
  junction, `New-Item` puede crear directorios vacíos en otra ubicación. La
  comprobación posterior (línea 2607) aborta con código 3 antes de escribir
  archivos, pero los directorios vacíos quedan creados.
- **Condiciones:** escritura sobre un directorio padre de la ruta y actuar entre
  la validación y la creación.
- **Clasificación:** técnicamente posible según el código; no reproducido.
- **Evidencia y limitaciones:** inspección estática de las líneas 2603-2607.
  Ninguna prueba cubre esta ventana.

#### T-2 — Después de revalidar el directorio de salida

- **Ventana:** desde `Assert-DirectoryInUse` (líneas 2607 y 2617-2618) hasta el
  final de la ejecución.
- **Recurso:** el estado (`Save-RunState`, líneas 1275-1277), los logs
  (`Write-StepLog`, línea 1299), `junit.xml` (línea 2073) y los JSON que
  escriben los procesos Python en el directorio de informes (línea 2309).
- **Efecto técnicamente posible:** escrituras o sobrescrituras de esos archivos,
  con sus nombres fijos, en una ubicación redirigida.
- **Condiciones:** escritura sobre el directorio de salida o sobre un padre suyo
  durante la ejecución.
- **Clasificación:** técnicamente posible según el código; no reproducido. Es el
  núcleo del riesgo aceptado.
- **Evidencia y limitaciones:** inspección estática. Esas escrituras no se
  revalidan una a una.

#### T-3a — Entre la revalidación del directorio Compose y la escritura del override

- **Ventana:** desde `Assert-DirectoryInUse` del directorio `compose` (línea
  2618) hasta las escrituras de las líneas 2313-2314.
- Dentro de la ventana se ejecutan, entre otras operaciones:
  - la persistencia del estado;
  - la comprobación del archivo Compose;
  - `docker version` y `docker compose version`;
  - la resolución del contexto;
  - la búsqueda de recursos preexistentes;
  - la comprobación del puerto;
  - la generación de credenciales.

  La ventana puede durar segundos.
- **Recurso:** `invocation.override.yml` y `empty.env`.
- **Efecto técnicamente posible:** pueden sobrescribirse archivos con esos
  nombres en el destino redirigido. La comprobación posterior
  (`Test-ComposeDirectoryClean`, línea 1822) aborta con código 10 antes de
  `compose config` y `compose up`.
- **Condiciones:** escritura sobre el directorio de salida o sobre un padre suyo
  durante la ventana.
- **Clasificación:** técnicamente posible según el código; no reproducido.
- **Evidencia y limitaciones:** inspección estática. Las pruebas de
  `Test-ComposeDirectoryClean` solo cubren condiciones estáticas.

#### T-3b — Después de revalidar el directorio Compose, entre `compose config` y `compose up`

- **Ventana:** desde la línea 1822 hasta `compose config` (línea 2323), y desde
  allí hasta `compose up` (línea 2333). Compose vuelve a leer el override y
  `empty.env` por su ruta en cada comando (líneas 1808-1810).
- **Recurso:** `invocation.override.yml` y `empty.env`.
- **Efecto técnicamente posible:**
  - Una sustitución antes de `config` la detecta
    `Test-ComposeRenderedConfig`, que compara el contenido y aborta con 10.
  - Una sustitución entre `config` y `up` haría que Compose ejecutara una
    configuración distinta de la validada, por ejemplo con un bind mount.
    `Update-Ownership` inspecciona volúmenes, no bind mounts.
- **Condiciones:** escritura sobre el directorio de salida o sobre un padre suyo
  y actuar entre ambos comandos.
- **Clasificación:** técnicamente posible según el código; no reproducido.
- **Evidencia y limitaciones:** inspección estática. Ninguna prueba cubre esta
  ventana.

#### T-4 — Entre la validación de `-Tests` y la ejecución de pytest

- **Ventana:** desde `Resolve-AuthorizedTestPath` (línea 496) hasta que pytest
  recoge las rutas (líneas 2053 y 2075).
- **Recurso:** las rutas de pruebas bajo `apps\…\tests` del repositorio.
- **Efecto técnicamente posible:** una sustitución de la ruta podría hacer que
  pytest ejecutara código de otra ubicación contra la base desechable. El correo
  sigue bloqueado y las credenciales son solo las de esa base.
- **Condiciones:** escritura sobre el repositorio durante la ejecución.
- **Clasificación:** técnicamente posible según el código; no reproducido.
- **Evidencia y limitaciones:** inspección estática. Las pruebas de `-Tests`
  cubren solo la validación inicial.

#### T-7 — Entre la validación de `StateFile` y el renombrado del temporal de recuperación

- **Ventana:** desde `Resolve-StateFilePath` (línea 2522) hasta la escritura del
  temporal (línea 2560) y `Move-Item -Force` (línea 2561), en
  `Update-RecoveryState`. Entre ambos momentos solo hay construcción en memoria
  del historial y del objeto `Recovery`.
- **Recurso:** `run-state.json` y `run-state.json.tmp` en el directorio del
  estado.
- **Efecto técnicamente posible:** el estado podría actualizarse en otra
  ubicación, sobrescribiendo archivos con esos nombres. El estado original
  quedaría sin el bloque `Recovery` y el rastro de auditoría quedaría
  incompleto. No afecta a la limpieza de recursos Docker, que ya concluyó y no
  depende de la ubicación del archivo, sino del HMAC y de la propiedad.
- **Condiciones:** escritura sobre el directorio del estado o sobre un padre
  suyo dentro de la ventana.
- **Clasificación:** técnicamente posible, no reproducido, ventana mínima.
- **Evidencia y limitaciones:** inspección estática de las líneas 2520-2561. La
  prueba del StateFile en un directorio enlazado solo cubre la detección previa.

### Observaciones adicionales

#### T-5 — Intérprete de `.venv`

- **Ubicación:** líneas 2150 y 2646.
- **Recurso:** `.venv\Scripts\python.exe`.
- **Hallazgo:** se comprueba solo con `Test-Path`, sin validar reparse points ni
  ubicación física. Es una **ausencia de control, no un TOCTOU**.
- **Efecto técnicamente posible:** que se ejecute otro intérprete.
- **Condiciones:** escritura sobre el repositorio.
- **Clasificación:** la ausencia del control está verificada estáticamente; el
  efecto no se ha reproducido.
- **Evidencia y limitaciones:** inspección estática. Ninguna prueba lo cubre.

#### T-6 — Contenido de `docker-compose.disposable.yml`

- **Ventana:** desde `compose config` (línea 2323) hasta `compose up` (línea
  2333).
- **Recurso:** el contenido de `docker-compose.disposable.yml`.
- **Hallazgo:** el contenido podría cambiar entre ambos comandos. Es un
  **TOCTOU de contenido, no de ubicación**.
- **Efecto técnicamente posible:** que `up` aplique una configuración no
  validada.
- **Condiciones:** escritura sobre el repositorio y actuar entre ambos comandos.
- **Clasificación:** técnicamente posible según el código; no reproducido.
- **Evidencia y limitaciones:** inspección estática. Ninguna prueba lo cubre.

## Evidencia y límites de lo demostrado

> Ninguna de las ventanas T-1 a T-7 ha sido reproducida. Su existencia se deduce de la inspección estática del código (versión con SHA-256 CA92A148…). Las pruebas existentes demuestran la detección de sustituciones presentes en el momento de la comprobación, no la ausencia de carreras posteriores.

**Evidencia histórica.** Pruebas Pester de
`tools/tests/run_permissions_disposable.Tests.ps1`, ejecutadas en M3-2.4.3 y
M3-2.4.4 antes de este registro. Durante D03 no se ejecutaron pruebas nuevas.

- **Validación de `-Tests` y de `OutputDirectory`** (líneas 503-554): rechazo
  en la validación inicial, incluida una junction dentro de `apps\…\tests` que
  apunta fuera del proyecto. **La prueba de symlinks se omitió por falta de
  privilegios.**
- **Pruebas AUD-013** (líneas 1609-1655):
  - raíz con espacios;
  - raíz que es una junction o que no existe (código 3);
  - detección de junctions y contención física de una ruta externa que apunta
    dentro del repositorio;
  - `StateFile` en un directorio enlazado (código 3).
- **Prueba "destino que cambia entre validación y uso"** (líneas 1640-1646): es
  una **simulación mediante mock** de `Resolve-SafeOutputDirectory` que devuelve
  una ruta ya enlazada. **No es la reproducción de una carrera real.**

**Límites:**

- No se comprobó la ACL efectiva de este equipo. La referencia a los permisos
  del perfil del usuario es una inferencia, no una comprobación.
- No se ha determinado el comportamiento de pytest ni de Compose ante una
  sustitución durante la ejecución.

## Decisión y trabajo futuro

- El riesgo residual queda documentado y aceptado para el alcance actual.
- No se corrige código en M3-2.4.4.
- No se ejecutan pruebas de carreras en esta fase.

**Posibles mejoras futuras.** Son propuestas, no implementaciones existentes;
cada una requiere un BUILD autorizado aparte:

1. Retener handles de directorio y operar mediante handles cuando sea viable.
2. Revalidar inmediatamente antes de cada escritura o usar operaciones
   relativas a handles.
3. Revisar ACL propias para `OutputDirectory`.
4. Validar reparse points y ubicación física de `.venv`.
5. Evitar que Compose relea una configuración modificable entre validación y
   ejecución, por ejemplo usando una copia controlada fuera del repositorio.
6. Incorporar pruebas controladas de sustitución entre validación y uso.

## Exclusiones

En el registro de esta decisión:

- No se modifica el código.
- No se modifica la configuración Docker.
- No se ejecutan Docker, Compose ni MariaDB.
- No se ejecuta el orquestador.
- No se crean junctions ni symlinks.
- No se ejecutan pruebas nuevas.
- No se modifica FARMS ni `config/platform.py`.

## Trazabilidad al código

- `tools/disposable_orchestrator_lib.ps1` (SHA-256 `CA92A148…`):
  - `Get-NormalizedFullPath`, `Test-IsPathWithin`, `Get-ReparsePointInChain`,
    `Get-FinalPathOrNull` y `FinalPath`.
  - `Get-NearestExistingFinalPath`, `Test-RepositoryRootSafe`,
    `Test-PathPhysicallyWithin` y `Assert-DirectoryInUse`.
  - `Resolve-AuthorizedTestPath`, `Resolve-SafeOutputDirectory` y
    `Resolve-StateFilePath`.
  - `Test-ComposeDirectoryClean`, `Save-RunState`, `Write-StepLog`,
    `Update-RecoveryState`, `Invoke-EnvironmentStages`, `Invoke-CleanupOnlyMode`
    e `Invoke-DisposableOrchestrator`.
- `tools/tests/run_permissions_disposable.Tests.ps1`:
  - "Validación de -Tests" y "Validación de OutputDirectory".
  - "AUD-013: rutas físicas y raíz del repositorio".

## Brechas de implementación

- Las ventanas T-1, T-2, T-3a, T-3b, T-4 y T-7 permanecen abiertas.
- Las observaciones T-5 y T-6 permanecen abiertas.
- No existen pruebas de carreras reales entre validación y uso.
