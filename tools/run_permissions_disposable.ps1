<#
.SYNOPSIS
    Orquestador de validación sobre una MariaDB desechable (endurecido, M3-2.4).

.DESCRIPTION
    Crea un contenedor MariaDB 11.8.8 desechable propio de esta invocación,
    demuestra en vivo que Django está conectado a esa base (preflight) y solo
    entonces ejecuta migraciones y pruebas autorizadas. Al terminar elimina
    exclusivamente los recursos cuya propiedad puede demostrar.

    Modos:
      (sin modo)        Crea el entorno, ejecuta el preflight y lo elimina.
      -Validate         Preflight + migrate (mismo proceso) + preflight posterior + pytest.
      -SqlMigrateOnly   Preflight + sqlmigrate trazabilidad 0007 (configuración aislada).
      -CleanupOnly      Elimina un entorno de una invocación anterior, solo con
                        -RunId y -StateFile (run-state.json) de esa invocación.
      -Validate -WithRedis
                        Como -Validate, con un Redis 7.4.12 desechable propio
                        (F10.3.4-A) como caché de Django en lugar de LocMemCache.

    Garantías:
      - SettingsModule: solo config.settings_test_disposable y
        config.settings_test_disposable_isolated.
      - El orquestador solo migra dentro de config.disposable_preflight tras un
        preflight OK sobre la conexión real (identidad del servidor, usuario,
        privilegios por SHOW GRANTS, esquema vacío, bloqueo de correo); nunca
        invoca manage.py.
      - Ruta adicional de migración: en -Validate, pytest-django con --reuse-db
        vuelve a llamar a migrate (setup_databases con keepdb) dentro de pytest.
        Esa ruta NO ejecuta el preflight completo; la protegen la guarda de
        conftest.py (--reuse-db obligatorio, --create-db prohibido, identidad en
        vivo antes de django_db_setup) y el preflight 'migrated' previo.
      - Contexto Docker: solo named pipes locales de Docker Desktop
        (npipe:////./pipe/docker_engine, dockerDesktopLinuxEngine,
        dockerDesktopWindowsEngine); se rechazan pipes remotos, tcp, ssh, etc.
      - Logs saneados: nunca se registra la salida completa de docker inspect
        (Config.Env) ni valores de variables con nombre sensible.
      - Procesos: un descendiente vivo tras el timeout o tras terminar el hijo
        impide declarar éxito; sus PID quedan en run-state.json
        (TerminationFailures).
      - Limpieza: plazo global CleanupTimeoutSeconds. Si se agota, los recursos
        restantes no se borran, el estado es CLEANUP_INCOMPLETE y el código es
        41 (o 42 si la etapa ya había fallado); recuperar con -CleanupOnly.
      - -Tests: rutas existentes bajo apps\...\tests, sin opciones de pytest.
      - Entornos hijo construidos desde cero: sin DOCKER_*, COMPOSE_*, PYTEST_*,
        PYTHON*, DJANGO_* ni credenciales heredadas; PYTHON_DOTENV_DISABLED=1.
      - Correo: variables SMTP/Brevo vacías, backend locmem y bloqueo de
        smtplib/requests en los procesos Python.
      - Propiedad: etiqueta agrotech.invocation-id (override generado fuera del
        repositorio); nunca se borra un recurso preexistente ni ajeno; sin -v.
      - Resultados, logs, JUnit y estado en OutputDirectory, fuera del
        repositorio; python -B, -p no:cacheprovider y -o addopts=.

.PARAMETER OutputDirectory
    Directorio de resultados (absoluto, fuera del repositorio, vacío o
    inexistente). Por defecto %LOCALAPPDATA%\agrotech-disposable\runs\<RunId>-<inv>.

.PARAMETER StateFile
    run-state.json de la invocación que creó el entorno (solo con -CleanupOnly).
    Debe ser auténtico: lo firma un HMAC con un token por ejecución cifrado con
    DPAPI (usuario actual de Windows). Aun así no autoriza por sí solo a
    borrar: cada recurso se revalida contra Docker (etiquetas, InvocationId y
    agrotech.owner-proof = SHA-256 del token). Los estados sin bloque Integrity
    (versiones anteriores) se rechazan con 50 y requieren limpieza manual.

.PARAMETER MariaDbImageDigest
    Obligatorio en los modos que crean el entorno. Digest del ÍNDICE OCI
    multiplataforma que publica la etiqueta oficial mariadb:11.8.8
    (sha256:<64 hex en minúscula>), es decir, el valor 'digest' de la etiqueta
    en Docker Hub y la columna Digest de 'docker image ls --digests'. NO es el
    digest de una plataforma concreta (p. ej. linux/amd64): con ese valor la
    comprobación de imagen local falla y se aborta con 10. Valor aprobado y
    fuentes: docs/decisions/DEC-08-fijacion-digest-mariadb-orquestador.md.
    La imagen se fija como mariadb:11.8.8@<digest> en el override, compose se
    ejecuta con --pull never y, si la imagen no está ya en local con ese
    digest, se aborta (10) sin descargar nada.

.PARAMETER WithRedis
    Solo con -Validate (F10.3.4-A). Añade docker-compose.disposable.redis.yml:
    un contenedor redis:7.4.12 propio de la invocación (mismo proyecto, misma
    red y mismas etiquetas de propiedad), publicado solo en 127.0.0.1:16379,
    con contraseña aleatoria por ejecución, datos en tmpfs y sin RDB ni AOF.
    Antes de migrar se comprueban su propiedad, la publicación del puerto, que
    exige autenticación y una marca con el RunId en la base 1; el preflight de
    Python repite la comprobación con la conexión real. REDIS_URL sigue vacía:
    la caché la construye config.settings_test_disposable con destino fijo.
    La limpieza y -CleanupOnly eliminan también este contenedor (estado de
    integridad v2). Sin -WithRedis, el comportamiento no cambia.

.PARAMETER RedisImageDigest
    Obligatorio con -WithRedis y rechazado sin él. Digest del ÍNDICE OCI de la
    etiqueta oficial redis:7.4.12 (sha256:<64 hex en minúscula>). Mismas reglas
    que -MariaDbImageDigest: la imagen debe estar ya en local con ese digest y
    nunca se descarga (10 si falta).

.FUNCTIONALITY
    Controles adicionales (M3-2.4.3):
      - Procesos: se crean suspendidos, se asignan a un Job KILL_ON_JOB_CLOSE y
        solo después se reanudan; si la asignación falla no llegan a ejecutarse.
      - Recursos Docker: la ausencia solo se deduce de un listado estructurado
        (ls --no-trunc --filter, salida validada); cualquier otro error es
        ambiguo y nunca autoriza un borrado.
      - Rutas: raíz del repositorio sin redirecciones; rutas físicas resueltas
        (junctions y symlinks) y revalidadas en el momento de uso.
      - Compose: directorio de proyecto limpio, exactamente dos -f, --env-file
        vacío, contexto fijado y validación de 'compose config' antes de crear.
      - Volúmenes: solo se borran volúmenes anónimos registrados en el
        contenedor propio, ya sin uso y marcados como anónimos o del proyecto;
        los demás se conservan y se informan (limpieza no completa).
      - Mutex Global\: una sola ejecución en toda la máquina.


.NOTES
    Códigos de salida (tabla central: tools/disposable_orchestrator_lib.ps1):
      0   OK                        Modo completado y limpieza correcta.
      1   (PowerShell)              No lo devuelve la tabla central. Lo produce PowerShell ante un error de
                                    enlace de parámetros (nombre o tipo inexistente) o una excepción no
                                    capturada fuera de los bloques controlados ($ErrorActionPreference = Stop).
      3   PARAMETERS_INVALID        Parámetros o combinación no permitida; no se crean recursos.
      10  PREFLIGHT_REJECTED        Preflight o identidad del entorno rechazados; sin migraciones ni pruebas.
      20  MIGRATION_FAILED          Falló migrate/sqlmigrate o su verificación posterior.
      30  TESTS_FAILED              pytest terminó con código distinto de 0.
      40  INFRASTRUCTURE_FAILED     Docker, timeout, terminación no confirmada, estado no persistido o error interno.
      41  CLEANUP_FAILED            La etapa terminó bien pero la limpieza falló o quedó incompleta.
      42  STAGE_AND_CLEANUP_FAILED  Fallaron la etapa y la limpieza.
      43  INTERRUPTED               Ejecución interrumpida; estado indeterminado (ver run-state.json).
      50  PREEXISTING_OR_OWNERSHIP  Recurso preexistente, puerto ocupado o propiedad no demostrada.

    Tras Ctrl+C, PowerShell ejecuta el bloque finally (limpieza de lo propio),
    pero el código de salida observado puede no ser 43: el estado persistido
    en run-state.json es la referencia.

    Estado final en run-state.json:
      NO_RESOURCES_CREATED  No se creó ningún recurso (nada que limpiar).
      CLEANUP_COMPLETED     Se crearon recursos y se eliminaron todos.
      CLEANUP_INCOMPLETE    Se agotó CleanupTimeoutSeconds; quedan recursos.
      CLEANUP_FAILED        Una operación de limpieza falló o se negó a borrar.
      INTERRUPTED           Ejecución interrumpida.
      RECOVERY_COMPLETED / RECOVERY_INCOMPLETE / RECOVERY_FAILED / RECOVERY_REFUSED
                            Resultado de -CleanupOnly, añadido al mismo archivo (campo Recovery).
#>
[CmdletBinding()]
param(
    [switch]$CleanupOnly,
    [string]$RunId,
    [switch]$Validate,
    [switch]$SqlMigrateOnly,
    [string]$HealthTimeoutSeconds,
    [string[]]$Tests = @("apps/permissions/tests"),
    [string]$SettingsModule = "config.settings_test_disposable",
    [string]$OutputDirectory,
    [string]$StateFile,
    [string]$MariaDbImageDigest,
    [switch]$WithRedis,
    [string]$RedisImageDigest,
    [string]$DockerCommandTimeoutSeconds,
    [string]$ComposeTimeoutSeconds,
    [string]$PreflightTimeoutSeconds,
    [string]$MigrateTimeoutSeconds,
    [string]$PytestTimeoutSeconds,
    [string]$CleanupTimeoutSeconds
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "disposable_orchestrator_lib.ps1")

$options = @{
    Root                = (Split-Path -Parent $PSScriptRoot)
    CleanupOnly         = [bool]$CleanupOnly
    RunId               = $RunId
    Validate            = [bool]$Validate
    SqlMigrateOnly      = [bool]$SqlMigrateOnly
    Tests               = @($Tests)
    TestsBound          = $PSBoundParameters.ContainsKey("Tests")
    SettingsModule      = $SettingsModule
    SettingsModuleBound = $PSBoundParameters.ContainsKey("SettingsModule")
    OutputDirectory     = $OutputDirectory
    StateFile           = $StateFile
    MariaDbImageDigest  = $MariaDbImageDigest
    WithRedis           = [bool]$WithRedis
    RedisImageDigest    = $RedisImageDigest
}
foreach ($name in @(
        "HealthTimeoutSeconds", "DockerCommandTimeoutSeconds", "ComposeTimeoutSeconds",
        "PreflightTimeoutSeconds", "MigrateTimeoutSeconds", "PytestTimeoutSeconds",
        "CleanupTimeoutSeconds")) {
    if ($PSBoundParameters.ContainsKey($name)) {
        $options[$name] = $PSBoundParameters[$name]
    }
}

# El último objeto del flujo de salida es el código; los mensajes van al host.
$exitCode = @(Invoke-DisposableOrchestrator -Options $options)[-1]
exit ([int]$exitCode)
