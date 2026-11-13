<#
.SYNOPSIS
    Funciones del orquestador desechable endurecido (M3-2.4).

.DESCRIPTION
    Se carga con dot-sourcing desde tools/run_permissions_disposable.ps1 y desde
    las pruebas Pester (3.4.0). No ejecuta nada al cargarse: solo define
    funciones y la tabla central de códigos de salida.

    Compatible con Windows PowerShell 5.1: sin operadores ternarios, sin '??',
    sin '&&'. Los procesos externos se ejecutan con un entorno hijo construido
    desde cero (lista blanca), con timeout, terminación del árbol de procesos
    confirmada y salida redactada.
#>

Set-StrictMode -Version Latest

# ---------------------------------------------------------------------------
# Tabla central de códigos de salida. config/disposable_preflight.py replica el
# subconjunto de los procesos hijos (0, 10, 20, 40); las pruebas verifican la
# coincidencia y que la ayuda del script la documenta.
# ---------------------------------------------------------------------------
$script:DisposableExitCodes = [ordered]@{
    OK                       = 0
    PARAMETERS_INVALID       = 3
    PREFLIGHT_REJECTED       = 10
    MIGRATION_FAILED         = 20
    TESTS_FAILED             = 30
    INFRASTRUCTURE_FAILED    = 40
    CLEANUP_FAILED           = 41
    STAGE_AND_CLEANUP_FAILED = 42
    INTERRUPTED              = 43
    PREEXISTING_OR_OWNERSHIP = 50
}

$script:AllowedSettingsModules = @(
    "config.settings_test_disposable",
    "config.settings_test_disposable_isolated"
)
$script:IsolatedSettingsModule = "config.settings_test_disposable_isolated"
$script:DefaultSettingsModule = "config.settings_test_disposable"
$script:RunIdPattern = "^[0-9a-f]{12}$"
$script:ContainerPort = 3317
$script:DbUser = "agrotech_perm_validation"
$script:LabelPurpose = "permissions-validation"

# Redis desechable opcional (F10.3.4-A, solo con -Validate -WithRedis). El
# destino (127.0.0.1:16379) y la versión los fijan también
# config.settings_test_disposable y config.disposable_preflight.
$script:RedisImageRepository = "redis"
$script:RedisImageTag = "7.4.12"
$script:RedisPort = 16379
$script:RedisContainerPort = 6379
$script:RedisComposeFileName = "docker-compose.disposable.redis.yml"
$script:RedisMarkerDb = 1
$script:RedisMarkerKey = "agrotech:disposable:run-id"

$script:TimeoutRanges = [ordered]@{
    HealthTimeoutSeconds        = @(10, 600)
    DockerCommandTimeoutSeconds = @(5, 300)
    ComposeTimeoutSeconds       = @(30, 1800)
    PreflightTimeoutSeconds     = @(30, 1800)
    MigrateTimeoutSeconds       = @(30, 3600)
    PytestTimeoutSeconds        = @(60, 14400)
    CleanupTimeoutSeconds       = @(30, 900)
}
$script:DefaultTimeouts = [ordered]@{
    HealthTimeoutSeconds        = 120
    DockerCommandTimeoutSeconds = 60
    ComposeTimeoutSeconds       = 300
    PreflightTimeoutSeconds     = 300
    MigrateTimeoutSeconds       = 900
    PytestTimeoutSeconds        = 3600
    CleanupTimeoutSeconds       = 180
}

# Variables del sistema que necesitan Docker CLI y Python en Windows. Todo lo
# demás (DOCKER_*, COMPOSE_*, PYTEST_*, PYTHON*, DJANGO_*, SMTP_*, BREVO_*,
# credenciales) queda fuera del entorno hijo por construcción.
$script:BaseEnvironmentAllowlist = @(
    "SystemRoot", "windir", "SystemDrive", "ComSpec", "PATH", "PATHEXT",
    "TEMP", "TMP", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "HOMEDRIVE",
    "HOMEPATH", "USERNAME", "USERDOMAIN", "COMPUTERNAME", "ProgramData",
    "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "CommonProgramFiles",
    "CommonProgramFiles(x86)", "CommonProgramW6432", "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE", "PROCESSOR_IDENTIFIER", "OS"
)

# Variables que el código de AGROTECH lee y que en un proceso desechable deben
# existir vacías: así ni el entorno heredado ni un .env pueden activarlas
# (python-dotenv no sobrescribe variables existentes).
$script:PythonForcedEmpty = @(
    "SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM", "BREVO_API_KEY",
    "BREVO_SMS_SENDER", "BREVO_WHATSAPP_SENDER", "BREVO_WHATSAPP_TEMPLATE_ID",
    "REDIS_URL", "DATABASE_URL"
)

function Get-DisposableExitCode {
    param([Parameter(Mandatory = $true)][string]$Name)
    if (-not $script:DisposableExitCodes.Contains($Name)) {
        throw "Código de salida desconocido: $Name"
    }
    return [int]$script:DisposableExitCodes[$Name]
}

# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------

function Get-NormalizedFullPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    $full = [System.IO.Path]::GetFullPath($Path)
    if ($full.Length -gt 3) {
        $full = $full.TrimEnd('\')
    }
    return $full
}

function Test-IsPathWithin {
    param(
        [Parameter(Mandatory = $true)][string]$Child,
        [Parameter(Mandatory = $true)][string]$Parent
    )
    $c = Get-NormalizedFullPath -Path $Child
    $p = Get-NormalizedFullPath -Path $Parent
    if ($c.Equals($p, [StringComparison]::OrdinalIgnoreCase)) {
        return $true
    }
    $prefix = $p.TrimEnd('\') + '\'
    return $c.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)
}

function Test-IsReparsePoint {
    param([Parameter(Mandatory = $true)][string]$Path)
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    if ($null -eq $item) {
        return $false
    }
    return [bool]($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint)
}

function Get-ReparsePointInChain {
    <#
    Devuelve el primer componente existente que es symlink o junction en la
    cadena de la ruta. Si se indica -From, empieza después de ese prefijo.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [string]$From
    )
    $full = Get-NormalizedFullPath -Path $Path
    $start = ""
    if ($From) {
        $start = Get-NormalizedFullPath -Path $From
        if (-not (Test-IsPathWithin -Child $full -Parent $start)) {
            return $full
        }
    }
    $root = [System.IO.Path]::GetPathRoot($full)
    $relative = $full.Substring($root.Length)
    $current = $root.TrimEnd('\')
    foreach ($segment in ($relative -split '\\')) {
        if (-not $segment) { continue }
        $current = $current + '\' + $segment
        if ($start -and -not (Test-IsPathWithin -Child $current -Parent $start)) {
            continue
        }
        if ($start -and $current.Equals($start, [StringComparison]::OrdinalIgnoreCase)) {
            continue
        }
        if (-not (Test-Path -LiteralPath $current)) {
            break
        }
        if (Test-IsReparsePoint -Path $current) {
            return $current
        }
    }
    return $null
}

function Get-FinalPathOrNull {
    <#
    Ruta física final (resuelve junctions y symlinks) de un archivo o
    directorio existente, normalizada; $null si no existe o no se puede
    resolver (AUD-013).
    #>
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    if (-not (Initialize-NativeSupport)) { return $null }
    $final = [AgrotechDisposableNative]::FinalPath((Get-NormalizedFullPath -Path $Path))
    if (-not $final) { return $null }
    return (Get-NormalizedFullPath -Path $final)
}

function Get-NearestExistingFinalPath {
    <# Ruta física de la ruta, o de su ancestro existente más cercano más el resto. #>
    param([Parameter(Mandatory = $true)][string]$Path)
    $current = Get-NormalizedFullPath -Path $Path
    $suffix = ""
    while ($current) {
        if (Test-Path -LiteralPath $current) {
            $final = Get-FinalPathOrNull -Path $current
            if (-not $final) { return $null }
            if ($suffix) { return (Get-NormalizedFullPath -Path ($final.TrimEnd('\') + '\' + $suffix)) }
            return $final
        }
        $leaf = Split-Path -Leaf $current
        $parent = Split-Path -Parent $current
        if (-not $parent -or $parent -eq $current) { return $null }
        if ($suffix) { $suffix = $leaf + '\' + $suffix } else { $suffix = $leaf }
        $current = $parent
    }
    return $null
}

function Test-RepositoryRootSafe {
    <#
    La raíz del repositorio debe existir, no atravesar symlinks ni junctions y
    su ruta física debe coincidir con la léxica (AUD-013). Devuelve un motivo
    de rechazo o $null.
    #>
    param([AllowEmptyString()][string]$Root)
    if ([string]::IsNullOrWhiteSpace($Root) -or -not (Test-Path -LiteralPath $Root -PathType Container)) {
        return "La raíz del repositorio no existe."
    }
    $full = Get-NormalizedFullPath -Path $Root
    if (Get-ReparsePointInChain -Path $full) { return "La raíz del repositorio atraviesa un symlink o junction." }
    $final = Get-FinalPathOrNull -Path $full
    if (-not $final) { return "No se pudo resolver la ruta física de la raíz del repositorio." }
    if (-not $final.Equals($full, [StringComparison]::OrdinalIgnoreCase)) {
        return "La raíz del repositorio está redirigida a otra ubicación física."
    }
    return $null
}

function Test-PathPhysicallyWithin {
    <# ¿La ruta física (o la de su ancestro existente) está dentro de Parent? #>
    param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)][string]$Parent)
    $final = Get-NearestExistingFinalPath -Path $Path
    $parentFinal = Get-FinalPathOrNull -Path $Parent
    if (-not $final -or -not $parentFinal) { return $null }
    return (Test-IsPathWithin -Child $final -Parent $parentFinal)
}

function Assert-DirectoryInUse {
    <#
    Revalidación justo antes de usar un directorio ya creado (AUD-013): debe
    existir, ser un directorio, no atravesar puntos de reparación, tener la
    misma ruta física que la léxica y estar fuera del repositorio. Reduce,
    pero no elimina, la ventana TOCTOU entre validación y uso.
    #>
    param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)][string]$Root)
    $full = Get-NormalizedFullPath -Path $Path
    if (-not (Test-Path -LiteralPath $full -PathType Container)) { throw "El directorio '$full' no existe o no es un directorio." }
    if (Get-ReparsePointInChain -Path $full) { throw "El directorio '$full' atraviesa un symlink o junction." }
    $final = Get-FinalPathOrNull -Path $full
    if (-not $final -or -not $final.Equals($full, [StringComparison]::OrdinalIgnoreCase)) { throw "El directorio '$full' está redirigido a otra ubicación física." }
    $within = Test-PathPhysicallyWithin -Path $full -Parent $Root
    if ($within -ne $false) { throw "El directorio '$full' está dentro del repositorio o no se pudo comprobar." }
}

function Resolve-AuthorizedTestPath {
    <#
    Valida un elemento de -Tests y devuelve la ruta relativa normalizada.
    Solo admite rutas existentes bajo <Root>\apps\ que contengan un segmento
    'tests'; si es un archivo debe llamarse test_*.py. Rechaza opciones,
    comodines, ids de nodo, rutas absolutas, traversal y puntos de reparación.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [AllowEmptyString()][string]$Item
    )
    if ([string]::IsNullOrWhiteSpace($Item)) {
        throw "Elemento vacío en -Tests."
    }
    $trimmed = $Item.Trim()
    if ($trimmed.StartsWith('-')) {
        throw "-Tests no admite opciones de pytest ('$trimmed')."
    }
    if ($trimmed -match '[\*\?\[\]]' -or $trimmed.Contains('::') -or $trimmed.Contains([char]0)) {
        throw "-Tests no admite comodines ni identificadores de nodo ('$trimmed')."
    }
    if ($trimmed -match '(^|[\\/])\.\.([\\/]|$)') {
        throw "-Tests no admite segmentos '..' ('$trimmed')."
    }
    if ([System.IO.Path]::IsPathRooted($trimmed)) {
        throw "-Tests solo admite rutas relativas al repositorio ('$trimmed')."
    }
    $rootFull = Get-NormalizedFullPath -Path $Root
    $full = Get-NormalizedFullPath -Path (Join-Path $rootFull $trimmed)
    $apps = Join-Path $rootFull "apps"
    if (-not (Test-IsPathWithin -Child $full -Parent $apps) -or $full.Equals((Get-NormalizedFullPath -Path $apps), [StringComparison]::OrdinalIgnoreCase)) {
        throw "-Tests solo admite rutas dentro de apps\ ('$trimmed')."
    }
    if (-not (Test-Path -LiteralPath $full)) {
        throw "La ruta de -Tests no existe ('$trimmed')."
    }
    $reparse = Get-ReparsePointInChain -Path $full -From $rootFull
    if ($reparse) {
        throw "La ruta de -Tests atraviesa un symlink o junction ('$trimmed')."
    }
    if ((Test-PathPhysicallyWithin -Path $full -Parent $apps) -ne $true) {
        throw "La ruta física de -Tests no está dentro de apps\ ('$trimmed')."
    }
    $relative = $full.Substring($rootFull.Length + 1)
    if (($relative -split '\\') -notcontains 'tests') {
        throw "La ruta de -Tests debe estar bajo un directorio 'tests' ('$trimmed')."
    }
    if (Test-Path -LiteralPath $full -PathType Leaf) {
        if ((Split-Path -Leaf $full) -notmatch '^test_.+\.py$') {
            throw "El archivo de -Tests debe llamarse test_*.py ('$trimmed')."
        }
    }
    return $relative
}

function Resolve-SafeOutputDirectory {
    <#
    Valida el directorio de resultados sin crearlo. Debe ser absoluto, estar
    fuera del repositorio (y no contenerlo), no ser una ubicación protegida,
    no atravesar symlinks ni junctions y, si existe, ser un directorio vacío.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [AllowEmptyString()][string]$Path,
        [switch]$AllowExisting
    )
    if ([string]::IsNullOrWhiteSpace($Path)) {
        throw "OutputDirectory vacío."
    }
    if (-not [System.IO.Path]::IsPathRooted($Path) -or $Path.StartsWith('\\')) {
        throw "OutputDirectory debe ser una ruta local absoluta."
    }
    $full = Get-NormalizedFullPath -Path $Path
    $rootFull = Get-NormalizedFullPath -Path $Root
    if (Test-IsPathWithin -Child $full -Parent $rootFull) {
        throw "OutputDirectory no puede estar dentro del repositorio."
    }
    if (Test-IsPathWithin -Child $rootFull -Parent $full) {
        throw "OutputDirectory no puede contener el repositorio."
    }
    if ($full.Length -le 3) {
        throw "OutputDirectory no puede ser la raíz de una unidad."
    }
    $protected = @()
    foreach ($name in @("windir", "ProgramFiles", "ProgramFiles(x86)", "ProgramData")) {
        $value = [Environment]::GetEnvironmentVariable($name)
        if ($value) { $protected += $value }
    }
    foreach ($p in $protected) {
        if (Test-IsPathWithin -Child $full -Parent $p) {
            throw "OutputDirectory no puede estar en una ubicación protegida del sistema."
        }
    }
    foreach ($exact in @($env:USERPROFILE, $env:LOCALAPPDATA, $env:APPDATA, $env:TEMP)) {
        if ($exact -and $full.Equals((Get-NormalizedFullPath -Path $exact), [StringComparison]::OrdinalIgnoreCase)) {
            throw "OutputDirectory debe ser un subdirectorio propio, no una carpeta compartida."
        }
    }
    $reparse = Get-ReparsePointInChain -Path $full
    if ($reparse) {
        throw "OutputDirectory atraviesa un symlink o junction."
    }
    if ((Test-PathPhysicallyWithin -Path $full -Parent $rootFull) -ne $false) {
        throw "La ruta física de OutputDirectory está dentro del repositorio o no se pudo comprobar."
    }
    if (Test-Path -LiteralPath $full) {
        if (-not (Test-Path -LiteralPath $full -PathType Container)) {
            throw "OutputDirectory existe y no es un directorio."
        }
        if (-not $AllowExisting) {
            $content = @(Get-ChildItem -LiteralPath $full -Force -ErrorAction Stop)
            if ($content.Count -gt 0) {
                throw "OutputDirectory existe y no está vacío."
            }
        }
    }
    return $full
}

# ---------------------------------------------------------------------------
# Parámetros
# ---------------------------------------------------------------------------

function Test-SettingsModuleAllowed {
    param([AllowEmptyString()][AllowNull()][string]$Name)
    if ([string]::IsNullOrWhiteSpace($Name)) {
        return $false
    }
    return ($script:AllowedSettingsModules -ccontains $Name)
}

$script:OrchestratorOptionKeys = @(
    "Root", "CleanupOnly", "RunId", "Validate", "SqlMigrateOnly", "Tests", "TestsBound",
    "SettingsModule", "SettingsModuleBound", "OutputDirectory", "StateFile", "MariaDbImageDigest",
    "WithRedis", "RedisImageDigest"
)

# Imagen prevista (R-ORQ-16). La etiqueta es la del compose; el digest lo
# aporta el operador con -MariaDbImageDigest y se fija en el override.
$script:ImageRepository = "mariadb"
$script:ImageTag = "11.8.8"
$script:ImageDigestPattern = '^sha256:[0-9a-f]{64}$'

function Complete-OrchestratorOptions {
    <#
    Devuelve una copia con todas las claves conocidas (ausentes = $null), de
    modo que StrictMode no falle al leer opciones no indicadas. Las claves
    desconocidas se descartan.
    #>
    param([Parameter(Mandatory = $true)][hashtable]$Options)
    $complete = @{}
    foreach ($key in @($script:OrchestratorOptionKeys) + @($script:TimeoutRanges.Keys)) {
        $complete[$key] = $null
        if ($Options.ContainsKey($key)) { $complete[$key] = $Options[$key] }
    }
    return $complete
}

function Test-OrchestratorOptions {
    <#
    Valida parámetros y combinaciones. Devuelve un objeto con Ok, Message y los
    valores normalizados. No crea archivos ni toca Docker.
    #>
    param([Parameter(Mandatory = $true)][hashtable]$Options)

    $Options = Complete-OrchestratorOptions -Options $Options
    $fail = {
        param($message)
        return [pscustomobject]@{ Ok = $false; Message = $message; Normalized = $null }
    }
    $root = $Options.Root
    $cleanupOnly = [bool]$Options.CleanupOnly
    $validate = [bool]$Options.Validate
    $sqlOnly = [bool]$Options.SqlMigrateOnly
    $testsBound = [bool]$Options.TestsBound
    $settingsBound = [bool]$Options.SettingsModuleBound
    $settingsModule = [string]$Options.SettingsModule
    if (-not $settingsBound -and [string]::IsNullOrEmpty($settingsModule)) {
        $settingsModule = $script:DefaultSettingsModule
    }

    $rootProblem = Test-RepositoryRootSafe -Root ([string]$root)
    if ($rootProblem) { return (& $fail $rootProblem) }
    if ($validate -and $sqlOnly) { return (& $fail "-Validate y -SqlMigrateOnly son incompatibles.") }
    $withRedis = [bool]$Options.WithRedis
    $imageReference = $null
    $redisImageReference = $null
    if ($cleanupOnly) {
        if ($validate -or $sqlOnly -or $testsBound -or $settingsBound -or $Options.OutputDirectory -or $Options.MariaDbImageDigest -or $withRedis -or $Options.RedisImageDigest) {
            return (& $fail "-CleanupOnly solo admite -RunId y -StateFile.")
        }
        if ([string]::IsNullOrWhiteSpace([string]$Options.RunId) -or $Options.RunId -cnotmatch $script:RunIdPattern) {
            return (& $fail "-CleanupOnly requiere -RunId con 12 caracteres hexadecimales en minúscula.")
        }
        if ([string]::IsNullOrWhiteSpace([string]$Options.StateFile)) {
            return (& $fail "-CleanupOnly requiere -StateFile de la invocación que creó el entorno.")
        }
    }
    elseif ($Options.StateFile) {
        return (& $fail "-StateFile solo se admite con -CleanupOnly.")
    }
    else {
        $digest = [string]$Options.MariaDbImageDigest
        if ([string]::IsNullOrWhiteSpace($digest)) {
            return (& $fail "-MariaDbImageDigest es obligatorio (sha256:<64 hex>) para crear el entorno; la imagen debe estar fijada por digest.")
        }
        if ($digest -cnotmatch $script:ImageDigestPattern) {
            return (& $fail "-MariaDbImageDigest no tiene el formato sha256:<64 hexadecimales en minúscula>.")
        }
        $imageReference = "{0}:{1}@{2}" -f $script:ImageRepository, $script:ImageTag, $digest
        $redisDigest = [string]$Options.RedisImageDigest
        if ($withRedis) {
            if (-not $validate) { return (& $fail "-WithRedis solo se admite con -Validate.") }
            if ([string]::IsNullOrWhiteSpace($redisDigest)) {
                return (& $fail "-RedisImageDigest es obligatorio (sha256:<64 hex>) con -WithRedis; la imagen debe estar fijada por digest.")
            }
            if ($redisDigest -cnotmatch $script:ImageDigestPattern) {
                return (& $fail "-RedisImageDigest no tiene el formato sha256:<64 hexadecimales en minúscula>.")
            }
            $redisImageReference = "{0}:{1}@{2}" -f $script:RedisImageRepository, $script:RedisImageTag, $redisDigest
        }
        elseif (-not [string]::IsNullOrEmpty($redisDigest)) {
            return (& $fail "-RedisImageDigest solo se admite con -WithRedis.")
        }
    }
    if ($testsBound -and -not $validate) { return (& $fail "-Tests solo se admite con -Validate.") }
    if ($sqlOnly) {
        if ($settingsBound -and $settingsModule -ne $script:IsolatedSettingsModule) {
            return (& $fail "-SqlMigrateOnly exige $($script:IsolatedSettingsModule).")
        }
        $settingsModule = $script:IsolatedSettingsModule
    }
    if (-not (Test-SettingsModuleAllowed -Name $settingsModule)) {
        return (& $fail "SettingsModule no autorizado: '$settingsModule'.")
    }
    if (-not $cleanupOnly -and $Options.RunId -and $Options.RunId -cnotmatch $script:RunIdPattern) {
        return (& $fail "-RunId debe tener 12 caracteres hexadecimales en minúscula.")
    }

    $timeouts = [ordered]@{}
    foreach ($key in $script:TimeoutRanges.Keys) {
        $value = $script:DefaultTimeouts[$key]
        if ($null -ne $Options[$key] -and [string]$Options[$key] -ne "") {
            $value = $Options[$key]
        }
        $parsed = 0
        if (-not [int]::TryParse([string]$value, [ref]$parsed)) {
            return (& $fail "$key debe ser un entero.")
        }
        $range = $script:TimeoutRanges[$key]
        if ($parsed -lt $range[0] -or $parsed -gt $range[1]) {
            return (& $fail "$key fuera de rango ($($range[0])-$($range[1])).")
        }
        $timeouts[$key] = $parsed
    }

    $tests = @()
    if ($validate) {
        $rawTests = @($Options.Tests)
        if ($rawTests.Count -eq 0) { return (& $fail "-Tests no puede estar vacío.") }
        foreach ($item in $rawTests) {
            try {
                $tests += Resolve-AuthorizedTestPath -Root $root -Item ([string]$item)
            }
            catch {
                return (& $fail $_.Exception.Message)
            }
        }
    }

    $outputDirectory = $null
    if ($Options.OutputDirectory) {
        try {
            $outputDirectory = Resolve-SafeOutputDirectory -Root $root -Path ([string]$Options.OutputDirectory)
        }
        catch {
            return (& $fail $_.Exception.Message)
        }
    }

    $stateFile = $null
    if ($cleanupOnly) {
        try {
            $stateFile = Resolve-StateFilePath -Root $root -Path ([string]$Options.StateFile)
        }
        catch {
            return (& $fail $_.Exception.Message)
        }
    }

    $mode = "EnvironmentOnly"
    if ($cleanupOnly) { $mode = "CleanupOnly" }
    elseif ($validate) { $mode = "Validate" }
    elseif ($sqlOnly) { $mode = "SqlMigrateOnly" }

    return [pscustomobject]@{
        Ok         = $true
        Message    = ""
        Normalized = [pscustomobject]@{
            Mode            = $mode
            SettingsModule  = $settingsModule
            RunId           = [string]$Options.RunId
            Tests           = $tests
            OutputDirectory = $outputDirectory
            StateFile       = $stateFile
            Timeouts        = $timeouts
            ImageReference  = $imageReference
            WithRedis       = $withRedis
            RedisImageReference = $redisImageReference
        }
    }
}

function Resolve-StateFilePath {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Path
    )
    if (-not [System.IO.Path]::IsPathRooted($Path)) {
        throw "-StateFile debe ser una ruta absoluta."
    }
    $full = Get-NormalizedFullPath -Path $Path
    if (Test-IsPathWithin -Child $full -Parent $Root) {
        throw "-StateFile no puede estar dentro del repositorio."
    }
    if (-not (Test-Path -LiteralPath $full -PathType Leaf)) {
        throw "-StateFile no existe."
    }
    if (Get-ReparsePointInChain -Path $full) {
        throw "-StateFile atraviesa un symlink o junction."
    }
    if ((Test-PathPhysicallyWithin -Path $full -Parent $Root) -ne $false) {
        throw "La ruta física de -StateFile está dentro del repositorio o no se pudo comprobar."
    }
    return $full
}

# ---------------------------------------------------------------------------
# Entorno de procesos hijos
# ---------------------------------------------------------------------------

function Get-BaseChildEnvironment {
    param([System.Collections.IDictionary]$Source)
    if ($null -eq $Source) {
        $Source = [Environment]::GetEnvironmentVariables()
    }
    $result = @{}
    foreach ($name in $script:BaseEnvironmentAllowlist) {
        foreach ($key in @($Source.Keys)) {
            if ([string]::Equals([string]$key, $name, [StringComparison]::OrdinalIgnoreCase)) {
                $result[$name] = [string]$Source[$key]
            }
        }
    }
    return $result
}

function New-DockerChildEnvironment {
    param(
        [Parameter(Mandatory = $true)]$Names,
        [Parameter(Mandatory = $true)][string]$DbPassword,
        [Parameter(Mandatory = $true)][string]$RootPassword,
        [System.Collections.IDictionary]$Source,
        [string]$RedisPassword
    )
    $envMap = Get-BaseChildEnvironment -Source $Source
    if (-not [string]::IsNullOrEmpty($RedisPassword)) {
        $envMap["DISPOSABLE_REDIS_CONTAINER_NAME"] = $Names.RedisContainerName
        $envMap["DISPOSABLE_REDIS_CONTAINER_HOSTNAME"] = $Names.RedisContainerHostname
        $envMap["DISPOSABLE_REDIS_PASSWORD"] = $RedisPassword
    }
    $envMap["DISPOSABLE_RUN_ID"] = $Names.RunId
    $envMap["DISPOSABLE_PROJECT_NAME"] = $Names.ProjectName
    $envMap["DISPOSABLE_CONTAINER_NAME"] = $Names.ContainerName
    $envMap["DISPOSABLE_CONTAINER_HOSTNAME"] = $Names.ContainerHostname
    $envMap["DISPOSABLE_NETWORK_NAME"] = $Names.NetworkName
    $envMap["DISPOSABLE_DB_NAME"] = $Names.DatabaseName
    $envMap["DISPOSABLE_DB_USER"] = $script:DbUser
    $envMap["DISPOSABLE_DB_PASSWORD"] = $DbPassword
    $envMap["DISPOSABLE_ROOT_PASSWORD"] = $RootPassword
    return $envMap
}

function New-PythonChildEnvironment {
    param(
        [Parameter(Mandatory = $true)]$Names,
        [Parameter(Mandatory = $true)][string]$SettingsModule,
        [Parameter(Mandatory = $true)][string]$DbPassword,
        [Parameter(Mandatory = $true)][string]$ReportDirectory,
        [System.Collections.IDictionary]$Source,
        [string]$RedisPassword
    )
    $envMap = Get-BaseChildEnvironment -Source $Source
    $envMap["DJANGO_SETTINGS_MODULE"] = $SettingsModule
    $envMap["DJANGO_DEBUG"] = "true"
    $envMap["AGROTECH_DISPOSABLE_CONFIRM"] = "1"
    $envMap["AGROTECH_DISPOSABLE_RUN_ID"] = $Names.RunId
    $envMap["AGROTECH_DISPOSABLE_DB_PASSWORD"] = $DbPassword
    $envMap["AGROTECH_DISPOSABLE_REPORT_DIR"] = $ReportDirectory
    $envMap["PYTHON_DOTENV_DISABLED"] = "1"
    $envMap["PYTHONDONTWRITEBYTECODE"] = "1"
    $envMap["PYTHONNOUSERSITE"] = "1"
    foreach ($name in $script:PythonForcedEmpty) {
        $envMap[$name] = ""
    }
    # REDIS_URL sigue vacía: con Redis, la caché la construye
    # config.settings_test_disposable con destino fijo y esta contraseña.
    if (-not [string]::IsNullOrEmpty($RedisPassword)) {
        $envMap["AGROTECH_DISPOSABLE_REDIS"] = "1"
        $envMap["AGROTECH_DISPOSABLE_REDIS_PASSWORD"] = $RedisPassword
    }
    return $envMap
}

function Get-DisposableNames {
    param([Parameter(Mandatory = $true)][string]$Id)
    return [pscustomobject]@{
        RunId             = $Id
        ProjectName       = "agrotech-permtest-$Id"
        ContainerName     = "agrotech-permtest-$Id"
        ContainerHostname = "agrotech-permtest-$Id"
        NetworkName       = "agrotech-permtest-$Id-net"
        DatabaseName      = "agrotech_permtest_$Id"
        RedisContainerName     = "agrotech-permtest-$Id-redis"
        RedisContainerHostname = "agrotech-redis-$Id"
    }
}

function Test-CtxWithRedis {
    <# Verdadero solo si el contexto declara Redis desechable (F10.3.4-A). #>
    param($Ctx)
    if ($Ctx -is [System.Collections.IDictionary]) { return ($Ctx.Contains("WithRedis") -and [bool]$Ctx["WithRedis"]) }
    return $false
}

function New-RandomHex {
    param([Parameter(Mandatory = $true)][ValidateRange(1, 128)][int]$ByteCount)
    $bytes = New-Object byte[] $ByteCount
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return (-join ($bytes | ForEach-Object { $_.ToString("x2") }))
}

# ---------------------------------------------------------------------------
# Procesos acotados
# ---------------------------------------------------------------------------

function ConvertTo-CommandLineArgument {
    <# Cita un argumento según las reglas de CommandLineToArgvW. #>
    param([AllowEmptyString()][string]$Value)
    if ($Value.Length -gt 0 -and $Value -notmatch '[\s"]') {
        return $Value
    }
    $builder = New-Object System.Text.StringBuilder
    [void]$builder.Append('"')
    $backslashes = 0
    foreach ($ch in $Value.ToCharArray()) {
        if ($ch -eq '\') {
            $backslashes++
            continue
        }
        if ($ch -eq '"') {
            [void]$builder.Append('\' * ($backslashes * 2 + 1))
            [void]$builder.Append('"')
        }
        else {
            [void]$builder.Append('\' * $backslashes)
            [void]$builder.Append($ch)
        }
        $backslashes = 0
    }
    [void]$builder.Append('\' * ($backslashes * 2))
    [void]$builder.Append('"')
    return $builder.ToString()
}

function Join-CommandLine {
    param([string[]]$ArgumentList)
    if ($null -eq $ArgumentList) { return "" }
    return (($ArgumentList | ForEach-Object { ConvertTo-CommandLineArgument -Value ([string]$_) }) -join ' ')
}

function Protect-SecretText {
    param([AllowEmptyString()][AllowNull()][string]$Text, [string[]]$Secrets)
    if ($null -eq $Text) { return "" }
    $result = $Text
    foreach ($secret in @($Secrets)) {
        if ($secret -and $secret.Length -ge 4) {
            $result = $result.Replace($secret, "<redactado>")
        }
    }
    return $result
}

function Protect-SensitiveText {
    <#
    Redacta secretos conocidos y, además, cualquier asignación con nombre
    sensible (PASSWORD, PWD, SECRET, TOKEN, API_KEY...), tanto en forma
    VAR=valor (p. ej. Config.Env de docker inspect) como en JSON "clave": "valor".
    Se aplica a todo lo que se escribe en logs, aunque los secretos no se
    conozcan (caso -CleanupOnly).
    #>
    param([AllowEmptyString()][AllowNull()][string]$Text, [string[]]$Secrets = @())
    $result = Protect-SecretText -Text $Text -Secrets $Secrets
    $names = '[A-Za-z0-9_.-]*(?:PASSWORD|PASSWD|PWD|SECRET|TOKEN|API_KEY|APIKEY|CREDENTIAL)[A-Za-z0-9_.-]*'
    $result = [regex]::Replace($result, '(?i)("' + $names + '"\s*:\s*)"(?:[^"\\]|\\.)*"', '$1"<redactado>"')
    $result = [regex]::Replace($result, '(?i)\b(' + $names + ')=([^\s"'',;]*)', '$1=<redactado>')
    $result = [regex]::Replace($result, "(?i)(IDENTIFIED\s+BY\s+(?:PASSWORD\s+)?)'[^']*'", '$1''<redactado>''')
    return $result
}

$script:NativeSupport = $null

function Initialize-NativeSupport {
    <#
    Compila (una vez por sesión) el puente nativo con:
    - creación SUSPENDIDA del proceso, asignación al Job Object y solo
      después ResumeThread (AUD-009): el hijo no ejecuta ninguna instrucción
      fuera del Job; si la asignación falla, se termina sin reanudarlo;
    - Job con KILL_ON_JOB_CLOSE y sin BREAKAWAY: los descendientes heredan el
      Job y no pueden salir de él;
    - consulta de los procesos activos del Job;
    - ruta final de un archivo o directorio (resuelve junctions y symlinks,
      AUD-013).
    Sin este soporte no se ejecuta ningún proceso (falla cerrado).
    #>
    if ($null -ne $script:NativeSupport) { return $script:NativeSupport }
    try {
        if (-not ("AgrotechDisposableNative" -as [type])) {
            Add-Type -ErrorAction Stop -TypeDefinition @"
using System;
using System.Text;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

public sealed class AgrotechSpawnResult {
    public int ProcessId;
    public IntPtr ProcessHandle;
    public SafeFileHandle StdoutRead;
    public SafeFileHandle StderrRead;
    public string Error;
}

public static class AgrotechDisposableNative {
    const int CREATE_SUSPENDED = 0x00000004;
    const int CREATE_UNICODE_ENVIRONMENT = 0x00000400;
    const int CREATE_NO_WINDOW = 0x08000000;
    const int STARTF_USESTDHANDLES = 0x00000100;
    const int HANDLE_FLAG_INHERIT = 0x00000001;
    const uint INFINITE_WAIT_OBJECT_0 = 0x00000000;

    [StructLayout(LayoutKind.Sequential)]
    struct SecurityAttributes { public int nLength; public IntPtr lpSecurityDescriptor; public int bInheritHandle; }
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    struct StartupInfo {
        public int cb; public string lpReserved; public string lpDesktop; public string lpTitle;
        public int dwX; public int dwY; public int dwXSize; public int dwYSize; public int dwXCountChars;
        public int dwYCountChars; public int dwFillAttribute; public int dwFlags; public short wShowWindow;
        public short cbReserved2; public IntPtr lpReserved2; public IntPtr hStdInput; public IntPtr hStdOutput; public IntPtr hStdError;
    }
    [StructLayout(LayoutKind.Sequential)]
    struct ProcessInformation { public IntPtr hProcess; public IntPtr hThread; public int dwProcessId; public int dwThreadId; }
    [StructLayout(LayoutKind.Sequential)]
    struct BasicLimit {
        public long PerProcessUserTimeLimit; public long PerJobUserTimeLimit; public uint LimitFlags;
        public UIntPtr MinimumWorkingSetSize; public UIntPtr MaximumWorkingSetSize; public uint ActiveProcessLimit;
        public UIntPtr Affinity; public uint PriorityClass; public uint SchedulingClass;
    }
    [StructLayout(LayoutKind.Sequential)]
    struct IoCounters { public ulong A; public ulong B; public ulong C; public ulong D; public ulong E; public ulong F; }
    [StructLayout(LayoutKind.Sequential)]
    struct ExtendedLimit {
        public BasicLimit Basic; public IoCounters Io; public UIntPtr ProcessMemoryLimit;
        public UIntPtr JobMemoryLimit; public UIntPtr PeakProcessMemoryUsed; public UIntPtr PeakJobMemoryUsed;
    }

    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool CreatePipe(out IntPtr readPipe, out IntPtr writePipe, ref SecurityAttributes attributes, int size);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool SetHandleInformation(IntPtr handle, int mask, int flags);
    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    static extern bool CreateProcessW(string application, StringBuilder commandLine, IntPtr processAttributes,
        IntPtr threadAttributes, bool inheritHandles, int flags, IntPtr environment, string currentDirectory,
        ref StartupInfo startupInfo, out ProcessInformation processInformation);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern int ResumeThread(IntPtr thread);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool TerminateProcess(IntPtr process, uint exitCode);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern uint WaitForSingleObject(IntPtr handle, uint milliseconds);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool GetExitCodeProcess(IntPtr process, out uint exitCode);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool CloseHandle(IntPtr handle);
    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    static extern IntPtr CreateJobObjectW(IntPtr attributes, string name);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool SetInformationJobObject(IntPtr job, int infoClass, IntPtr info, uint length);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool TerminateJobObject(IntPtr job, uint exitCode);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool QueryInformationJobObject(IntPtr job, int infoClass, IntPtr info, int length, out int returned);
    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    static extern SafeFileHandle CreateFileW(string name, int access, int share, IntPtr security, int disposition, int flags, IntPtr template);
    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    static extern int GetFinalPathNameByHandleW(SafeFileHandle handle, StringBuilder path, int size, int flags);

    public static IntPtr CreateKillOnCloseJob() {
        IntPtr job = CreateJobObjectW(IntPtr.Zero, null);
        if (job == IntPtr.Zero) { return job; }
        ExtendedLimit info = new ExtendedLimit();
        info.Basic.LimitFlags = 0x2000; // KILL_ON_JOB_CLOSE, sin BREAKAWAY_OK
        int length = Marshal.SizeOf(typeof(ExtendedLimit));
        IntPtr pointer = Marshal.AllocHGlobal(length);
        try {
            Marshal.StructureToPtr(info, pointer, false);
            if (!SetInformationJobObject(job, 9, pointer, (uint)length)) { CloseHandle(job); return IntPtr.Zero; }
        } finally { Marshal.FreeHGlobal(pointer); }
        return job;
    }

    public static bool TerminateJob(IntPtr job) { return TerminateJobObject(job, 1); }
    public static void CloseJob(IntPtr job) { if (job != IntPtr.Zero) { CloseHandle(job); } }
    public static void CloseProcess(IntPtr process) { if (process != IntPtr.Zero) { CloseHandle(process); } }

    public static int ActiveProcessCount(IntPtr job) {
        int length = 48;
        IntPtr buffer = Marshal.AllocHGlobal(length);
        try {
            int returned;
            if (!QueryInformationJobObject(job, 1, buffer, length, out returned)) { return -1; }
            return Marshal.ReadInt32(buffer, 40);
        } finally { Marshal.FreeHGlobal(buffer); }
    }

    public static int[] ProcessIds(IntPtr job) {
        int capacity = 256;
        int length = 8 + capacity * IntPtr.Size;
        IntPtr buffer = Marshal.AllocHGlobal(length);
        try {
            int returned;
            if (!QueryInformationJobObject(job, 3, buffer, length, out returned)) { return new int[0]; }
            int count = Marshal.ReadInt32(buffer, 4);
            int[] ids = new int[count];
            for (int i = 0; i < count; i++) { ids[i] = (int)Marshal.ReadIntPtr(buffer, 8 + i * IntPtr.Size).ToInt64(); }
            return ids;
        } finally { Marshal.FreeHGlobal(buffer); }
    }

    public static bool WaitProcess(IntPtr process, int milliseconds) {
        return WaitForSingleObject(process, (uint)milliseconds) == INFINITE_WAIT_OBJECT_0;
    }

    public static int ExitCode(IntPtr process) {
        uint code;
        if (!GetExitCodeProcess(process, out code)) { return -1; }
        return unchecked((int)code);
    }

    static IntPtr MakePipe(out IntPtr parentEnd, bool parentReads) {
        SecurityAttributes sa = new SecurityAttributes();
        sa.nLength = Marshal.SizeOf(typeof(SecurityAttributes));
        sa.bInheritHandle = 1;
        IntPtr readEnd; IntPtr writeEnd;
        if (!CreatePipe(out readEnd, out writeEnd, ref sa, 0)) { parentEnd = IntPtr.Zero; return IntPtr.Zero; }
        if (parentReads) { SetHandleInformation(readEnd, HANDLE_FLAG_INHERIT, 0); parentEnd = readEnd; return writeEnd; }
        SetHandleInformation(writeEnd, HANDLE_FLAG_INHERIT, 0); parentEnd = writeEnd; return readEnd;
    }

    public static AgrotechSpawnResult StartSuspendedInJob(string application, string commandLine, string workingDirectory, string environmentBlock, IntPtr job) {
        AgrotechSpawnResult result = new AgrotechSpawnResult();
        IntPtr outParent, errParent, inParent;
        IntPtr outChild = MakePipe(out outParent, true);
        IntPtr errChild = MakePipe(out errParent, true);
        IntPtr inChild = MakePipe(out inParent, false);
        if (outChild == IntPtr.Zero || errChild == IntPtr.Zero || inChild == IntPtr.Zero) {
            result.Error = "no se pudieron crear las tuberías de E/S";
            return result;
        }
        StartupInfo si = new StartupInfo();
        si.cb = Marshal.SizeOf(typeof(StartupInfo));
        si.dwFlags = STARTF_USESTDHANDLES;
        si.hStdInput = inChild; si.hStdOutput = outChild; si.hStdError = errChild;
        IntPtr env = Marshal.StringToHGlobalUni(environmentBlock);
        ProcessInformation pi;
        bool created;
        try {
            created = CreateProcessW(application, new StringBuilder(commandLine), IntPtr.Zero, IntPtr.Zero, true,
                CREATE_SUSPENDED | CREATE_UNICODE_ENVIRONMENT | CREATE_NO_WINDOW, env, workingDirectory, ref si, out pi);
        } finally { Marshal.FreeHGlobal(env); }
        CloseHandle(outChild); CloseHandle(errChild); CloseHandle(inChild); CloseHandle(inParent);
        if (!created) {
            CloseHandle(outParent); CloseHandle(errParent);
            result.Error = "CreateProcess falló (error " + Marshal.GetLastWin32Error() + ")";
            return result;
        }
        if (job == IntPtr.Zero || !AssignProcessToJobObject(job, pi.hProcess)) {
            int assignError = Marshal.GetLastWin32Error();
            // El hijo sigue suspendido: no ha ejecutado código. Se termina sin reanudar.
            TerminateProcess(pi.hProcess, 1);
            WaitForSingleObject(pi.hProcess, 10000);
            CloseHandle(pi.hThread); CloseHandle(pi.hProcess);
            CloseHandle(outParent); CloseHandle(errParent);
            result.Error = "no se pudo asignar el proceso al Job (error " + assignError + "); no se reanudó";
            return result;
        }
        if (ResumeThread(pi.hThread) == -1) {
            TerminateProcess(pi.hProcess, 1);
            CloseHandle(pi.hThread); CloseHandle(pi.hProcess);
            CloseHandle(outParent); CloseHandle(errParent);
            result.Error = "ResumeThread falló";
            return result;
        }
        CloseHandle(pi.hThread);
        result.ProcessId = pi.dwProcessId;
        result.ProcessHandle = pi.hProcess;
        result.StdoutRead = new SafeFileHandle(outParent, true);
        result.StderrRead = new SafeFileHandle(errParent, true);
        return result;
    }

    public static string FinalPath(string path) {
        // FILE_READ_ATTRIBUTES, compartir todo, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS
        using (SafeFileHandle handle = CreateFileW(path, 0x80, 0x7, IntPtr.Zero, 3, 0x02000000, IntPtr.Zero)) {
            if (handle.IsInvalid) { return null; }
            StringBuilder buffer = new StringBuilder(1024);
            int length = GetFinalPathNameByHandleW(handle, buffer, buffer.Capacity, 0);
            if (length <= 0 || length >= buffer.Capacity) { return null; }
            string value = buffer.ToString();
            if (value.StartsWith(@"\\?\UNC\")) { return @"\\" + value.Substring(8); }
            if (value.StartsWith(@"\\?\")) { return value.Substring(4); }
            return value;
        }
    }
}
"@
        }
        $script:NativeSupport = $true
    }
    catch {
        $script:NativeSupport = $false
    }
    return $script:NativeSupport
}

function New-ProcessJob {
    <# Job con KILL_ON_JOB_CLOSE (aislado para poder simular su fallo). #>
    return [AgrotechDisposableNative]::CreateKillOnCloseJob()
}

function Close-ProcessJob {
    param($Job)
    if ($null -ne $Job -and $Job -ne [IntPtr]::Zero) { [AgrotechDisposableNative]::CloseJob($Job) }
}

function Get-JobProcessIds {
    param($Job)
    if ($null -eq $Job -or $Job -eq [IntPtr]::Zero) { return @() }
    return @([AgrotechDisposableNative]::ProcessIds($Job))
}

function Wait-JobEmpty {
    <# Devuelve los PID que siguen en el Job tras el plazo (vacío = terminado). #>
    param($Job, [int]$TimeoutSeconds = 15)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        if ([AgrotechDisposableNative]::ActiveProcessCount($Job) -eq 0) { return @() }
        Start-Sleep -Milliseconds 200
    } while ((Get-Date) -lt $deadline)
    return @(Get-JobProcessIds -Job $Job)
}

function ConvertTo-EnvironmentBlock {
    <# Bloque de entorno Unicode ordenado: CLAVE=valor\0...\0 #>
    param([Parameter(Mandatory = $true)][hashtable]$Environment)
    $keys = @($Environment.Keys | ForEach-Object { [string]$_ } | Where-Object { $_ -and -not $_.Contains("=") } | Sort-Object)
    $builder = New-Object System.Text.StringBuilder
    foreach ($key in $keys) {
        [void]$builder.Append($key).Append("=").Append([string]$Environment[$key]).Append([char]0)
    }
    if ($keys.Count -eq 0) { [void]$builder.Append([char]0) }
    [void]$builder.Append([char]0)
    return $builder.ToString()
}

function Stop-ProcessTree {
    <# Termina todo el Job y, como refuerzo, el árbol por PID. #>
    param(
        [Parameter(Mandatory = $true)][int]$ProcessId,
        [int[]]$Descendants = @(),
        $Job
    )
    if ($null -ne $Job -and $Job -ne [IntPtr]::Zero) {
        [void][AgrotechDisposableNative]::TerminateJob($Job)
    }
    try {
        $killInfo = New-Object System.Diagnostics.ProcessStartInfo
        $killInfo.FileName = Join-Path $env:SystemRoot "System32\taskkill.exe"
        $killInfo.Arguments = "/PID $ProcessId /T /F"
        $killInfo.UseShellExecute = $false
        $killInfo.CreateNoWindow = $true
        $killInfo.RedirectStandardOutput = $true
        $killInfo.RedirectStandardError = $true
        $killer = [System.Diagnostics.Process]::Start($killInfo)
        [void]$killer.WaitForExit(10000)
        $killer.Dispose()
    }
    catch { }
    foreach ($id in @($Descendants)) {
        Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
    }
}

function Invoke-BoundedProcess {
    <#
    Ejecuta un proceso SUSPENDIDO, lo asigna a un Job KILL_ON_JOB_CLOSE y solo
    entonces lo reanuda (AUD-009). Si no hay soporte nativo, si no se puede
    crear el Job o si la asignación falla, el proceso no llega a ejecutarse
    (StartError). Al vencer el timeout o si quedan descendientes tras la salida
    del hijo, termina el Job y solo confirma la terminación si el Job queda
    vacío. Cerrar el Job mata cualquier proceso restante.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$ArgumentList = @(),
        [Parameter(Mandatory = $true)][int]$TimeoutSeconds,
        [Parameter(Mandatory = $true)][hashtable]$Environment,
        [string]$WorkingDirectory,
        [string[]]$Redact = @(),
        [int]$TerminationGraceSeconds = 15
    )
    $result = [pscustomobject]@{
        ExitCode             = $null
        TimedOut             = $false
        TerminationConfirmed = $true
        Survivors            = @()
        LingeringDescendants = @()
        Stdout               = ""
        Stderr               = ""
        DurationMs           = 0
        StartError           = $null
    }
    if (-not (Initialize-NativeSupport)) {
        $result.StartError = "soporte nativo no disponible: no se ejecuta el proceso"
        return $result
    }
    if (-not $WorkingDirectory) { $WorkingDirectory = (Get-Location).ProviderPath }
    $job = New-ProcessJob
    if ($null -eq $job -or $job -eq [IntPtr]::Zero) {
        $result.StartError = "no se pudo crear el Job Object: no se ejecuta el proceso"
        return $result
    }
    $watch = [System.Diagnostics.Stopwatch]::StartNew()
    $commandLine = (ConvertTo-CommandLineArgument -Value $FilePath) + " " + (Join-CommandLine -ArgumentList $ArgumentList)
    $spawn = [AgrotechDisposableNative]::StartSuspendedInJob($FilePath, $commandLine.TrimEnd(), $WorkingDirectory, (ConvertTo-EnvironmentBlock -Environment $Environment), $job)
    if ($spawn.Error) {
        Close-ProcessJob -Job $job
        $result.StartError = $spawn.Error
        return $result
    }
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    $outReader = New-Object System.IO.StreamReader((New-Object System.IO.FileStream($spawn.StdoutRead, [System.IO.FileAccess]::Read, 4096, $false)), $utf8)
    $errReader = New-Object System.IO.StreamReader((New-Object System.IO.FileStream($spawn.StderrRead, [System.IO.FileAccess]::Read, 4096, $false)), $utf8)
    $outTask = $outReader.ReadToEndAsync()
    $errTask = $errReader.ReadToEndAsync()
    try {
        $timeoutMs = [int][Math]::Min([int64]$TimeoutSeconds * 1000, [int64][int]::MaxValue)
        if ([AgrotechDisposableNative]::WaitProcess($spawn.ProcessHandle, $timeoutMs)) {
            $result.ExitCode = [AgrotechDisposableNative]::ExitCode($spawn.ProcessHandle)
            # El hijo terminó, pero un descendiente puede seguir en el Job (AUD-008).
            $lingering = @(Get-JobProcessIds -Job $job)
            if ($lingering.Count -gt 0) {
                $result.LingeringDescendants = $lingering
                Stop-ProcessTree -ProcessId $spawn.ProcessId -Descendants $lingering -Job $job
                $survivors = @(Wait-JobEmpty -Job $job -TimeoutSeconds $TerminationGraceSeconds)
                $result.Survivors = $survivors
                $result.TerminationConfirmed = ($survivors.Count -eq 0)
            }
        }
        else {
            $result.TimedOut = $true
            $tree = @(Get-JobProcessIds -Job $job)
            Stop-ProcessTree -ProcessId $spawn.ProcessId -Descendants $tree -Job $job
            $survivors = @(Wait-JobEmpty -Job $job -TimeoutSeconds $TerminationGraceSeconds)
            $result.Survivors = $survivors
            $result.TerminationConfirmed = ($survivors.Count -eq 0)
        }
    }
    finally {
        # Cerrar el Job mata cualquier proceso que siga dentro (KILL_ON_JOB_CLOSE).
        Close-ProcessJob -Job $job
        try {
            if ($outTask.Wait(10000)) { $result.Stdout = Protect-SecretText -Text $outTask.Result -Secrets $Redact }
        }
        catch { $result.Stdout = "" }
        try {
            if ($errTask.Wait(10000)) { $result.Stderr = Protect-SecretText -Text $errTask.Result -Secrets $Redact }
        }
        catch { $result.Stderr = "" }
        $outReader.Dispose()
        $errReader.Dispose()
        [AgrotechDisposableNative]::CloseProcess($spawn.ProcessHandle)
        $watch.Stop()
        $result.DurationMs = [int]$watch.ElapsedMilliseconds
    }
    return $result
}

function Write-Utf8File {
    param([Parameter(Mandatory = $true)][string]$Path, [AllowEmptyString()][string]$Content)
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Content, $encoding)
}

function Initialize-DpapiSupport {
    try { Add-Type -AssemblyName System.Security -ErrorAction Stop; return $true } catch { return $false }
}

function ConvertTo-HexString {
    param([byte[]]$Bytes)
    return (-join ($Bytes | ForEach-Object { $_.ToString("x2") }))
}

function New-OwnerSecret {
    <#
    Token aleatorio de 256 bits por ejecución (AUD-014). Se guarda solo
    cifrado con DPAPI (usuario actual de Windows; entropía = InvocationId)
    en run-state.json, fuera del repositorio. Su SHA-256 es la etiqueta
    agrotech.owner-proof del contenedor y la red. El token en claro solo vive
    en memoria y nunca se registra.
    #>
    param([Parameter(Mandatory = $true)][string]$InvocationId)
    if (-not (Initialize-DpapiSupport)) { throw "DPAPI no disponible." }
    $token = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($token) } finally { $rng.Dispose() }
    $entropy = [System.Text.Encoding]::UTF8.GetBytes($InvocationId)
    $protected = [System.Security.Cryptography.ProtectedData]::Protect($token, $entropy, [System.Security.Cryptography.DataProtectionScope]::CurrentUser)
    return [pscustomobject]@{
        Token = $token
        Proof = Get-OwnerProof -Token $token
        Protected = [Convert]::ToBase64String($protected)
    }
}

function Get-OwnerProof {
    param([Parameter(Mandatory = $true)][byte[]]$Token)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try { return ConvertTo-HexString -Bytes $sha.ComputeHash($Token) } finally { $sha.Dispose() }
}

function Unprotect-OwnerToken {
    <# Descifra el token; falla si el estado es de otro usuario o fue alterado. #>
    param([Parameter(Mandatory = $true)][string]$Protected, [Parameter(Mandatory = $true)][string]$InvocationId)
    if (-not (Initialize-DpapiSupport)) { throw "DPAPI no disponible." }
    $bytes = [Convert]::FromBase64String($Protected)
    $entropy = [System.Text.Encoding]::UTF8.GetBytes($InvocationId)
    return [System.Security.Cryptography.ProtectedData]::Unprotect($bytes, $entropy, [System.Security.Cryptography.DataProtectionScope]::CurrentUser)
}

function Get-StateCanonicalText {
    <#
    Campos de run-state.json protegidos por el HMAC. v1: entorno sin Redis
    (formato original, sin cambios). v2 (F10.3.4-A): añade el nombre y el ID
    del contenedor Redis.
    #>
    param($RunId, $InvocationId, $ProjectName, $ContainerName, $NetworkName, $DatabaseName, $ContainerId, $NetworkId, [string[]]$Volumes = @(),
        [ValidateSet("v1", "v2")][string]$Version = "v1", $RedisContainerName, $RedisContainerId)
    $volumeText = (@($Volumes | Where-Object { $_ } | Sort-Object) -join ",")
    $fields = @($Version, $RunId, $InvocationId, $ProjectName, $ContainerName, $NetworkName, $DatabaseName, $ContainerId, $NetworkId, $volumeText)
    if ($Version -eq "v2") { $fields += @($RedisContainerName, $RedisContainerId) }
    return ($fields | ForEach-Object { [string]$_ }) -join "`n"
}

function Get-CtxIntegrityVersion {
    param($Ctx)
    if (Test-CtxWithRedis -Ctx $Ctx) { return "v2" }
    return "v1"
}

function Get-CtxRedisContainerId {
    param($Ctx)
    if ($Ctx.Owned -is [System.Collections.IDictionary] -and $Ctx.Owned.Contains("RedisContainerId")) { return $Ctx.Owned["RedisContainerId"] }
    return $null
}

function Get-StateMac {
    param([Parameter(Mandatory = $true)][byte[]]$Token, [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Text)
    $hmac = New-Object System.Security.Cryptography.HMACSHA256 (, $Token)
    try { return [Convert]::ToBase64String($hmac.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($Text))) } finally { $hmac.Dispose() }
}

function Test-FixedTimeEqual {
    param([AllowEmptyString()][AllowNull()][string]$A, [AllowEmptyString()][AllowNull()][string]$B)
    if ($null -eq $A -or $null -eq $B -or $A.Length -ne $B.Length) { return $false }
    $diff = 0
    for ($i = 0; $i -lt $A.Length; $i++) { $diff = $diff -bor ([int][char]$A[$i] -bxor [int][char]$B[$i]) }
    return ($diff -eq 0)
}

function Get-CtxCanonicalText {
    param($Ctx)
    return Get-StateCanonicalText -RunId $Ctx.Names.RunId -InvocationId $Ctx.InvocationId -ProjectName $Ctx.Names.ProjectName `
        -ContainerName $Ctx.Names.ContainerName -NetworkName $Ctx.Names.NetworkName -DatabaseName $Ctx.Names.DatabaseName `
        -ContainerId $Ctx.Owned.ContainerId -NetworkId $Ctx.Owned.NetworkId -Volumes @($Ctx.Owned.Volumes) `
        -Version (Get-CtxIntegrityVersion -Ctx $Ctx) -RedisContainerName $Ctx.Names.RedisContainerName -RedisContainerId (Get-CtxRedisContainerId -Ctx $Ctx)
}

function Test-StateIntegrity {
    <#
    Verifica que un run-state.json es auténtico (AUD-014) y devuelve el token
    descifrado, o un motivo de rechazo. Exige: bloque Integrity v1, nombres
    iguales a los derivados del RunId indicado, DPAPI del mismo usuario e
    InvocationId, y HMAC válido. El resultado NO autoriza por sí solo a borrar:
    cada recurso se revalida después contra Docker (etiquetas y owner-proof).
    #>
    param([Parameter(Mandatory = $true)]$State, [Parameter(Mandatory = $true)]$Names, [Parameter(Mandatory = $true)][string]$InvocationId)
    $integrity = Get-JsonValue -Object $State -Path @("Integrity")
    if ($null -eq $integrity) { return [pscustomobject]@{ Ok = $false; Reason = "el estado no tiene bloque de integridad"; Token = $null; WithRedis = $false } }
    $version = [string](Get-JsonValue -Object $integrity -Path @("Version"))
    if ($version -cne "v1" -and $version -cne "v2") { return [pscustomobject]@{ Ok = $false; Reason = "versión de integridad no soportada"; Token = $null; WithRedis = $false } }
    $pairs = @(@("ProjectName", $Names.ProjectName), @("ContainerName", $Names.ContainerName), @("NetworkName", $Names.NetworkName), @("DatabaseName", $Names.DatabaseName))
    $stateRedisName = Get-JsonValue -Object $State -Path @("RedisContainerName")
    $stateRedisId = Get-JsonValue -Object $State -Path @("Owned", "RedisContainerId")
    if ($version -eq "v2") { $pairs += , @("RedisContainerName", $Names.RedisContainerName) }
    elseif ($stateRedisName -or $stateRedisId) {
        # v1 no protege los campos de Redis: su presencia indica manipulación.
        return [pscustomobject]@{ Ok = $false; Reason = "campos de Redis sin protección de integridad"; Token = $null; WithRedis = $false }
    }
    foreach ($pair in $pairs) {
        if ([string](Get-JsonValue -Object $State -Path @($pair[0])) -cne $pair[1]) {
            return [pscustomobject]@{ Ok = $false; Reason = ("el campo " + $pair[0] + " no corresponde al RunId"); Token = $null; WithRedis = $false }
        }
    }
    try {
        $token = Unprotect-OwnerToken -Protected ([string](Get-JsonValue -Object $integrity -Path @("ProtectedToken"))) -InvocationId $InvocationId
    }
    catch {
        return [pscustomobject]@{ Ok = $false; Reason = "el token no se puede descifrar (otro usuario, otro InvocationId o dato alterado)"; Token = $null; WithRedis = $false }
    }
    $volumes = @(Get-JsonValue -Object $State -Path @("Owned", "Volumes") | ForEach-Object { [string]$_ })
    $text = Get-StateCanonicalText -RunId $Names.RunId -InvocationId $InvocationId -ProjectName $Names.ProjectName `
        -ContainerName $Names.ContainerName -NetworkName $Names.NetworkName -DatabaseName $Names.DatabaseName `
        -ContainerId ([string](Get-JsonValue -Object $State -Path @("Owned", "ContainerId"))) `
        -NetworkId ([string](Get-JsonValue -Object $State -Path @("Owned", "NetworkId"))) -Volumes $volumes `
        -Version $version -RedisContainerName ([string]$stateRedisName) -RedisContainerId ([string]$stateRedisId)
    $mac = Get-StateMac -Token $token -Text $text
    if (-not (Test-FixedTimeEqual -A $mac -B ([string](Get-JsonValue -Object $integrity -Path @("Mac"))))) {
        return [pscustomobject]@{ Ok = $false; Reason = "HMAC del estado no válido (estado alterado)"; Token = $null; WithRedis = $false }
    }
    return [pscustomobject]@{ Ok = $true; Reason = ""; Token = $token; WithRedis = ($version -eq "v2") }
}

function Save-RunState {
    param([Parameter(Mandatory = $true)]$Ctx)
    if (-not $Ctx.StateFile) { return }
    # En Windows PowerShell 5.1, @($List[T]) dentro de un literal de hashtable
    # falla ("Los tipos de argumentos no coinciden"); se usa ToArray().
    $snapshot = @{
        RunId          = $Ctx.Names.RunId
        InvocationId   = $Ctx.InvocationId
        Mode           = $Ctx.Mode
        SettingsModule = $Ctx.SettingsModule
        Tests          = @($Ctx.Tests)
        DockerContext  = $Ctx.DockerContext
        ProjectName    = $Ctx.Names.ProjectName
        ContainerName  = $Ctx.Names.ContainerName
        NetworkName    = $Ctx.Names.NetworkName
        DatabaseName   = $Ctx.Names.DatabaseName
        Owned          = @{
            ContainerId = $Ctx.Owned.ContainerId
            NetworkId   = $Ctx.Owned.NetworkId
            Volumes     = @($Ctx.Owned.Volumes)
        }
        State          = $Ctx.State
        StageCode      = $Ctx.StageCode
        StageMessage   = $Ctx.StageMessage
        Cleanup        = $Ctx.Cleanup
        ExitCode       = $Ctx.ExitCode
        History        = $Ctx.History.ToArray()
    }
    if ($Ctx.ContainsKey("TerminationFailures")) {
        $snapshot["TerminationFailures"] = $Ctx.TerminationFailures.ToArray()
    }
    if (Test-CtxWithRedis -Ctx $Ctx) {
        $snapshot["RedisContainerName"] = $Ctx.Names.RedisContainerName
        $snapshot.Owned["RedisContainerId"] = Get-CtxRedisContainerId -Ctx $Ctx
    }
    if ($Ctx.ContainsKey("OwnerToken") -and $null -ne $Ctx.OwnerToken) {
        # Integridad (AUD-014): token cifrado con DPAPI y HMAC de los campos que
        # identifican los recursos. El token en claro nunca se escribe.
        $snapshot["Integrity"] = @{
            Version = (Get-CtxIntegrityVersion -Ctx $Ctx); Algorithm = "HMAC-SHA256"; Protection = "DPAPI-CurrentUser"
            ProtectedToken = $Ctx.ProtectedToken
            Mac = (Get-StateMac -Token $Ctx.OwnerToken -Text (Get-CtxCanonicalText -Ctx $Ctx))
        }
    }
    $tmp = $Ctx.StateFile + ".tmp"
    Write-Utf8File -Path $tmp -Content ($snapshot | ConvertTo-Json -Depth 6)
    Move-Item -LiteralPath $tmp -Destination $Ctx.StateFile -Force
}

function Set-RunState {
    param([Parameter(Mandatory = $true)]$Ctx, [Parameter(Mandatory = $true)][string]$State, [string]$Detail = "")
    $Ctx.State = $State
    $Ctx.History.Add(@{ State = $State; At = (Get-Date).ToUniversalTime().ToString("o"); Detail = $Detail })
    Save-RunState -Ctx $Ctx
}

function Write-StepLog {
    param($Ctx, [string]$Step, $Result)
    if (-not $Ctx.LogDirectory) { return }
    $secrets = @()
    if ($Ctx.ContainsKey("Secrets")) { $secrets = @($Ctx.Secrets) }
    $survivors = ""
    if ($Result.PSObject.Properties["Survivors"]) { $survivors = (@($Result.Survivors) -join ",") }
    $stdout = Protect-SensitiveText -Text ([string]$Result.Stdout) -Secrets $secrets
    $stderr = Protect-SensitiveText -Text ([string]$Result.Stderr) -Secrets $secrets
    $text = "exit=$($Result.ExitCode) timedOut=$($Result.TimedOut) terminationConfirmed=$($Result.TerminationConfirmed) survivors=$survivors durationMs=$($Result.DurationMs)`n--- stdout ---`n$stdout`n--- stderr ---`n$stderr`n"
    $safe = ($Step -replace '[^A-Za-z0-9_.-]', '_')
    $Ctx.LogSequence++
    Write-Utf8File -Path (Join-Path $Ctx.LogDirectory ("{0:D3}-{1}.log" -f $Ctx.LogSequence, $safe)) -Content $text
}

# ---------------------------------------------------------------------------
# Docker (todas las llamadas pasan por Invoke-DockerCommand)
# ---------------------------------------------------------------------------

function Register-ProcessTermination {
    <#
    Anota en el contexto los pasos cuyo árbol de procesos no terminó de forma
    confirmada (AUD-008). Solo PID y nombre del paso, nunca argumentos.
    #>
    param($Ctx, [string]$Step, $Result)
    if ($null -eq $Ctx -or -not ($Ctx -is [hashtable]) -or -not $Ctx.ContainsKey("TerminationFailures")) { return }
    if ($null -eq $Result -or $Result.TerminationConfirmed) { return }
    $phase = "stage"
    if ($Ctx.ContainsKey("Phase") -and $Ctx.Phase) { $phase = [string]$Ctx.Phase }
    $Ctx.TerminationFailures.Add(@{
            Step      = $Step
            Phase     = $phase
            Survivors = @($Result.Survivors | ForEach-Object { [int]$_ })
            At        = (Get-Date).ToUniversalTime().ToString("o")
        })
}

function Get-DockerExecutable {
    $command = Get-Command docker -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $command) { return $null }
    return $command.Source
}

function Invoke-DockerCommand {
    param(
        [Parameter(Mandatory = $true)]$Ctx,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [int]$TimeoutSeconds,
        [switch]$NoContext,
        [switch]$NoOutputLog
    )
    if (-not $TimeoutSeconds) { $TimeoutSeconds = $Ctx.Timeouts.DockerCommandTimeoutSeconds }
    $full = @()
    if (-not $NoContext) {
        if (-not $Ctx.DockerContext) { throw "Contexto Docker no resuelto." }
        $full += @("--context", $Ctx.DockerContext)
    }
    $full += $Arguments
    $result = Invoke-BoundedProcess -FilePath $Ctx.DockerExe -ArgumentList $full -TimeoutSeconds $TimeoutSeconds -Environment $Ctx.DockerEnvironment -WorkingDirectory $Ctx.ComposeDirectory -Redact $Ctx.Secrets
    $logged = $result
    if ($NoOutputLog) {
        # La salida (p. ej. SHOW GRANTS) puede contener hashes de contraseña.
        $logged = [pscustomobject]@{
            ExitCode = $result.ExitCode; TimedOut = $result.TimedOut
            TerminationConfirmed = $result.TerminationConfirmed; Survivors = @($result.Survivors)
            DurationMs = $result.DurationMs; Stdout = "<omitido>"; Stderr = "<omitido>"
        }
    }
    Write-StepLog -Ctx $Ctx -Step ("docker-" + $Arguments[0] + "-" + $Arguments[1]) -Result $logged
    Register-ProcessTermination -Ctx $Ctx -Step ("docker " + $Arguments[0] + " " + $Arguments[1]) -Result $result
    return $result
}

function Write-ResourceLog {
    <#
    Resumen diagnóstico de un docker inspect sin su salida completa (AUD-006):
    estado, ID, nombre, hostname y solo etiquetas agrotech.* y
    com.docker.compose.*; stderr saneado y truncado. Nunca Config.Env.
    #>
    param($Ctx, [string]$Kind, [string]$Ref, $Resource, $Result)
    $labels = @()
    foreach ($key in @($Resource.Labels.Keys | Sort-Object)) {
        if ($key -like "agrotech.*" -or $key -like "com.docker.compose.*") {
            $labels += ("{0}={1}" -f $key, $Resource.Labels[$key])
        }
    }
    $stderr = ""
    if ($Resource.State -ne "Present") {
        $stderr = ([string]$Result.Stderr).Trim()
        if ($stderr.Length -gt 300) { $stderr = $stderr.Substring(0, 300) }
    }
    $summary = "kind=$Kind ref=$Ref state=$($Resource.State) id=$($Resource.Id) name=$($Resource.Name) hostname=$($Resource.Hostname)`nlabels:`n" + ($labels -join "`n")
    Write-StepLog -Ctx $Ctx -Step ("inspect-" + $Kind) -Result ([pscustomobject]@{
            ExitCode = $Result.ExitCode; TimedOut = $Result.TimedOut
            TerminationConfirmed = $Result.TerminationConfirmed; Survivors = @($Result.Survivors)
            DurationMs = $Result.DurationMs; Stdout = $summary; Stderr = $stderr
        })
}

function ConvertTo-LabelTable {
    param($Labels)
    $table = @{}
    if ($null -eq $Labels) { return $table }
    foreach ($property in $Labels.PSObject.Properties) {
        $table[$property.Name] = [string]$property.Value
    }
    return $table
}

function Get-DockerErrorCategory {
    <#
    Clasifica un fallo de la CLI de Docker solo con fines de diagnóstico.
    Ninguna categoría equivale a "recurso inexistente" (AUD-012).
    #>
    param([AllowEmptyString()][AllowNull()][string]$Stderr)
    $text = [string]$Stderr
    if ($text -match '(?i)error during connect|cannot connect to the docker daemon|is the docker daemon running|open //\./pipe|docker desktop is not running') { return "DaemonUnavailable" }
    if ($text -match '(?i)permission denied|access is denied|acceso denegado') { return "PermissionDenied" }
    if ($text -match '(?i)context\b.*\b(not found|does not exist)|current context') { return "ContextInvalid" }
    return "Unknown"
}

function New-ResourceRecord {
    param([string]$State, [string]$Kind, [string]$Detail = "", [string]$Category = "")
    return [pscustomobject]@{
        State = $State; Kind = $Kind; Id = $null; Name = $null; Labels = @{}; Hostname = $null
        Mounts = @(); Detail = $Detail; Category = $Category
    }
}

function Find-DockerResourceId {
    <#
    Localiza un recurso por ID completo o nombre exacto con un listado
    estructurado (ls --no-trunc --filter --format). "Absent" SOLO si el comando
    terminó con 0, la salida es válida y no hay coincidencia exacta. Cualquier
    otro caso es Error (falla cerrado): nunca se infiere ausencia de stderr.
    #>
    param(
        [Parameter(Mandatory = $true)]$Ctx,
        [Parameter(Mandatory = $true)][ValidateSet("container", "network", "volume")][string]$Kind,
        [Parameter(Mandatory = $true)][string]$Ref,
        [int]$TimeoutSeconds
    )
    $byId = ($Kind -ne "volume" -and $Ref -cmatch '^[0-9a-f]{64}$')
    $filter = "name=$Ref"
    if ($byId) { $filter = "id=$Ref" }
    switch ($Kind) {
        "container" { $arguments = @("container", "ls", "-a", "--no-trunc", "--filter", $filter, "--format", "{{.ID}}|{{.Names}}") }
        "network" { $arguments = @("network", "ls", "--no-trunc", "--filter", $filter, "--format", "{{.ID}}|{{.Name}}") }
        "volume" { $arguments = @("volume", "ls", "--filter", $filter, "--format", "{{.Name}}") }
    }
    $r = Invoke-DockerCommand -Ctx $Ctx -Arguments $arguments -TimeoutSeconds $TimeoutSeconds
    if ($r.StartError -or $r.TimedOut) { return [pscustomobject]@{ Status = "Error"; Id = $null; Category = "NoResponse" } }
    if ($r.ExitCode -ne 0) { return [pscustomobject]@{ Status = "Error"; Id = $null; Category = (Get-DockerErrorCategory -Stderr $r.Stderr) } }
    $found = @()
    foreach ($raw in ([string]$r.Stdout -split "`r?`n")) {
        $line = $raw.Trim()
        if (-not $line) { continue }
        if ($Kind -eq "volume") {
            if ($line -notmatch '^[A-Za-z0-9][A-Za-z0-9_.-]*$') { return [pscustomobject]@{ Status = "Error"; Id = $null; Category = "MalformedOutput" } }
            if ($line -ceq $Ref) { $found += $line }
            continue
        }
        $match = [regex]::Match($line, '^(?<id>[0-9a-f]{64})\|(?<names>[^|\s]+)$')
        if (-not $match.Success) { return [pscustomobject]@{ Status = "Error"; Id = $null; Category = "MalformedOutput" } }
        $id = $match.Groups['id'].Value
        $names = @($match.Groups['names'].Value -split ',')
        if (($byId -and $id -ceq $Ref) -or (-not $byId -and ($names -ccontains $Ref))) { $found += $id }
    }
    if ($found.Count -eq 0) { return [pscustomobject]@{ Status = "Absent"; Id = $null; Category = "" } }
    if ($found.Count -gt 1) { return [pscustomobject]@{ Status = "Error"; Id = $null; Category = "Ambiguous" } }
    return [pscustomobject]@{ Status = "Found"; Id = $found[0]; Category = "" }
}

function Get-DockerResource {
    <#
    Estado de un contenedor o red: Present, Absent o Error. Primero se localiza
    con un listado estructurado (AUD-012) y luego se inspecciona por ID exacto.
    La salida de inspect incluye Config.Env (contraseñas): no se registra nunca,
    solo un resumen saneado (AUD-006).
    #>
    param(
        [Parameter(Mandatory = $true)]$Ctx,
        [Parameter(Mandatory = $true)][ValidateSet("container", "network")][string]$Kind,
        [Parameter(Mandatory = $true)][string]$Ref,
        [int]$TimeoutSeconds
    )
    $lookup = Find-DockerResourceId -Ctx $Ctx -Kind $Kind -Ref $Ref -TimeoutSeconds $TimeoutSeconds
    $r = [pscustomobject]@{ ExitCode = 0; TimedOut = $false; TerminationConfirmed = $true; Survivors = @(); DurationMs = 0; Stdout = ""; Stderr = "" }
    if ($lookup.Status -eq "Absent") {
        $resource = New-ResourceRecord -State "Absent" -Kind $Kind
    }
    elseif ($lookup.Status -ne "Found") {
        $resource = New-ResourceRecord -State "Error" -Kind $Kind -Detail "listado no concluyente" -Category $lookup.Category
    }
    else {
        $r = Invoke-DockerCommand -Ctx $Ctx -Arguments @($Kind, "inspect", $lookup.Id) -TimeoutSeconds $TimeoutSeconds -NoOutputLog
        if ($r.StartError -or $r.TimedOut) {
            $resource = New-ResourceRecord -State "Error" -Kind $Kind -Detail "inspect sin respuesta" -Category "NoResponse"
        }
        elseif ($r.ExitCode -ne 0) {
            # Incluso "No such ..." aquí es Error: el recurso existía en el listado.
            $resource = New-ResourceRecord -State "Error" -Kind $Kind -Detail "inspect falló" -Category (Get-DockerErrorCategory -Stderr $r.Stderr)
        }
        else {
            try {
                $parsed = @(ConvertFrom-Json -InputObject $r.Stdout)
                $items = @($parsed | ForEach-Object { $_ })
                if ($items.Count -ne 1) { throw "inspect devolvió $($items.Count) objetos" }
                $item = $items[0]
                if ([string]$item.Id -cne $lookup.Id) { throw "ID de inspect distinto del listado" }
                $resource = New-ResourceRecord -State "Present" -Kind $Kind
                $resource.Id = [string]$item.Id
                if ($Kind -eq "container") {
                    $resource.Name = ([string]$item.Name).TrimStart('/')
                    $resource.Labels = ConvertTo-LabelTable -Labels $item.Config.Labels
                    $resource.Hostname = [string]$item.Config.Hostname
                    $mounts = @()
                    $rawMounts = Get-JsonValue -Object $item -Path @("Mounts")
                    foreach ($mount in @($rawMounts)) {
                        if ($null -eq $mount) { continue }
                        $mounts += [pscustomobject]@{
                            Type = [string](Get-JsonValue -Object $mount -Path @("Type"))
                            Name = [string](Get-JsonValue -Object $mount -Path @("Name"))
                            Destination = [string](Get-JsonValue -Object $mount -Path @("Destination"))
                        }
                    }
                    $resource.Mounts = $mounts
                }
                else {
                    $resource.Name = [string]$item.Name
                    $resource.Labels = ConvertTo-LabelTable -Labels $item.Labels
                }
            }
            catch {
                $resource = New-ResourceRecord -State "Error" -Kind $Kind -Detail "salida de inspect no interpretable" -Category "MalformedOutput"
            }
        }
    }
    Write-ResourceLog -Ctx $Ctx -Kind $Kind -Ref $Ref -Resource $resource -Result $r
    return $resource
}

function Get-ResourceOwnership {
    <#
    Owned: nombre, etiquetas base, RunId, InvocationId y la prueba de
    propiedad (agrotech.owner-proof = SHA-256 del token de la ejecución,
    AUD-014) coinciden; en contenedores también el hostname. Foreign: existe
    pero no es de esta invocación. Ambiguous: no se puede determinar.
    #>
    param([Parameter(Mandatory = $true)]$Resource, [Parameter(Mandatory = $true)]$Expected)
    if ($Resource.State -eq "Absent") { return "Absent" }
    if ($Resource.State -ne "Present") { return "Ambiguous" }
    $expectedName = $Expected.NetworkName
    if ($Resource.Kind -eq "container") { $expectedName = $Expected.ContainerName }
    if ($Resource.Name -cne $expectedName) { return "Foreign" }
    $labels = $Resource.Labels
    $base = @{ "agrotech.disposable" = "true"; "agrotech.run-id" = $Expected.RunId; "agrotech.purpose" = $script:LabelPurpose }
    foreach ($key in $base.Keys) {
        if (-not $labels.ContainsKey($key) -or $labels[$key] -cne $base[$key]) { return "Foreign" }
    }
    if (-not $labels.ContainsKey("agrotech.invocation-id") -or $labels["agrotech.invocation-id"] -cne $Expected.InvocationId) {
        return "Foreign"
    }
    if ([string]::IsNullOrEmpty($Expected.OwnerProof) -or -not $labels.ContainsKey("agrotech.owner-proof") -or $labels["agrotech.owner-proof"] -cne $Expected.OwnerProof) {
        return "Foreign"
    }
    if ($Resource.Kind -eq "container" -and $Resource.Hostname -cne $Expected.ContainerHostname) {
        return "Ambiguous"
    }
    return "Owned"
}

function Get-ExpectedOwnership {
    param(
        [Parameter(Mandatory = $true)]$Names,
        [Parameter(Mandatory = $true)][string]$InvocationId,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$OwnerProof
    )
    return [pscustomobject]@{
        RunId = $Names.RunId; InvocationId = $InvocationId; OwnerProof = $OwnerProof
        ContainerName = $Names.ContainerName; ContainerHostname = $Names.ContainerHostname
        NetworkName = $Names.NetworkName; ProjectName = $Names.ProjectName
    }
}

function Get-ExpectedRedisOwnership {
    <#
    Propiedad esperada del contenedor Redis (F10.3.4-A): mismas etiquetas y
    prueba de propiedad que el resto de la invocación, con su nombre y
    hostname propios.
    #>
    param(
        [Parameter(Mandatory = $true)]$Names,
        [Parameter(Mandatory = $true)][string]$InvocationId,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$OwnerProof
    )
    $expected = Get-ExpectedOwnership -Names $Names -InvocationId $InvocationId -OwnerProof $OwnerProof
    $expected.ContainerName = $Names.RedisContainerName
    $expected.ContainerHostname = $Names.RedisContainerHostname
    return $expected
}

function Get-OwnedVolumeCandidates {
    <#
    Volúmenes montados en el contenedor propio (R-ORQ-8). Solo los de tipo
    volume con nombre de 64 hexadecimales (anónimos) son candidatos a borrar;
    el resto (nombrados, bind, tmpfs) nunca se borra y se informa.
    #>
    param($Resource)
    $candidates = @()
    $foreign = @()
    foreach ($mount in @($Resource.Mounts)) {
        if ($mount.Type -ne "volume") { continue }
        if ($mount.Name -cmatch '^[0-9a-f]{64}$') { $candidates += $mount.Name }
        else { $foreign += $mount.Name }
    }
    return [pscustomobject]@{ Anonymous = $candidates; Named = $foreign }
}

function Remove-OwnedVolume {
    <#
    Borra un volumen anónimo SOLO si: estaba montado en el contenedor propio
    (registrado al crearlo), su nombre es anónimo, existe de forma
    inequívoca, ningún contenedor lo usa ya y sus etiquetas lo marcan como
    anónimo o del proyecto Compose de esta ejecución. Nunca usa -f ni borrado
    global. Devuelve Removed, AlreadyAbsent, Kept:<motivo>, Failed o Incomplete.
    #>
    param(
        [Parameter(Mandatory = $true)]$Ctx,
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)]$Expected,
        [Nullable[datetime]]$Deadline
    )
    if ($Name -cnotmatch '^[0-9a-f]{64}$') { return "Kept:NotAnonymous" }
    $timeout = Get-CleanupStepTimeout -Ctx $Ctx -Deadline $Deadline
    if ($timeout -le 0) { return "Incomplete" }
    $lookup = Find-DockerResourceId -Ctx $Ctx -Kind volume -Ref $Name -TimeoutSeconds $timeout
    if ($lookup.Status -eq "Absent") { return "AlreadyAbsent" }
    if ($lookup.Status -ne "Found") { return "Kept:Ambiguous" }
    $timeout = Get-CleanupStepTimeout -Ctx $Ctx -Deadline $Deadline
    if ($timeout -le 0) { return "Incomplete" }
    $users = Invoke-DockerCommand -Ctx $Ctx -Arguments @("container", "ls", "-a", "--no-trunc", "--filter", "volume=$Name", "--format", "{{.ID}}") -TimeoutSeconds $timeout
    if ($users.StartError -or $users.TimedOut -or $users.ExitCode -ne 0) { return "Kept:Ambiguous" }
    if (([string]$users.Stdout).Trim()) { return "Kept:InUse" }
    $timeout = Get-CleanupStepTimeout -Ctx $Ctx -Deadline $Deadline
    if ($timeout -le 0) { return "Incomplete" }
    $inspect = Invoke-DockerCommand -Ctx $Ctx -Arguments @("volume", "inspect", $Name) -TimeoutSeconds $timeout -NoOutputLog
    if ($inspect.StartError -or $inspect.TimedOut -or $inspect.ExitCode -ne 0) { return "Kept:Ambiguous" }
    try {
        $item = @(@(ConvertFrom-Json -InputObject $inspect.Stdout) | ForEach-Object { $_ })[0]
        if ([string]$item.Name -cne $Name) { return "Kept:Ambiguous" }
        $labels = ConvertTo-LabelTable -Labels (Get-JsonValue -Object $item -Path @("Labels"))
    }
    catch { return "Kept:Ambiguous" }
    $anonymous = $labels.ContainsKey("com.docker.volume.anonymous")
    $ownProject = ($labels.ContainsKey("com.docker.compose.project") -and $labels["com.docker.compose.project"] -ceq $Expected.ProjectName)
    $otherProject = ($labels.ContainsKey("com.docker.compose.project") -and -not $ownProject)
    if ($otherProject -or -not ($anonymous -or $ownProject)) { return "Kept:NotProvenOwned" }
    $timeout = Get-CleanupStepTimeout -Ctx $Ctx -Deadline $Deadline
    if ($timeout -le 0) { return "Incomplete" }
    $rm = Invoke-DockerCommand -Ctx $Ctx -Arguments @("volume", "rm", $Name) -TimeoutSeconds $timeout
    if (-not $rm.TerminationConfirmed) { return "Failed:TerminationUnconfirmed" }
    $timeout = Get-CleanupStepTimeout -Ctx $Ctx -Deadline $Deadline
    if ($timeout -le 0) { return "Incomplete" }
    $after = Find-DockerResourceId -Ctx $Ctx -Kind volume -Ref $Name -TimeoutSeconds $timeout
    if ($after.Status -eq "Absent") { return "Removed" }
    return "Failed"
}

function Get-CleanupClock {
    <# Reloj de la limpieza (aislado para poder simularlo en pruebas). #>
    return [DateTime]::UtcNow
}

function Get-CleanupStepTimeout {
    <#
    Timeout del siguiente comando de limpieza: el menor entre el de Docker y el
    tiempo restante del plazo global. 0 significa plazo agotado (AUD-007).
    #>
    param([Parameter(Mandatory = $true)]$Ctx, [Nullable[datetime]]$Deadline)
    $perCommand = [int]$Ctx.Timeouts.DockerCommandTimeoutSeconds
    if ($null -eq $Deadline) { return $perCommand }
    $remaining = [int][Math]::Floor((([datetime]$Deadline) - (Get-CleanupClock)).TotalSeconds)
    if ($remaining -le 0) { return 0 }
    return [Math]::Min($perCommand, $remaining)
}

function Remove-OwnedResource {
    <#
    Borra por ID solo si la propiedad se revalida justo antes. Nunca usa -v.
    Con -Deadline, ningún comando supera el plazo global de limpieza.
    Devuelve Removed, AlreadyAbsent, Refused:<motivo>, Failed,
    Failed:TerminationUnconfirmed o Incomplete (plazo agotado).
    #>
    param(
        [Parameter(Mandatory = $true)]$Ctx,
        [Parameter(Mandatory = $true)][ValidateSet("container", "network")][string]$Kind,
        [Parameter(Mandatory = $true)][string]$Id,
        [Parameter(Mandatory = $true)]$Expected,
        [Nullable[datetime]]$Deadline
    )
    $timeout = Get-CleanupStepTimeout -Ctx $Ctx -Deadline $Deadline
    if ($timeout -le 0) { return "Incomplete" }
    $resource = Get-DockerResource -Ctx $Ctx -Kind $Kind -Ref $Id -TimeoutSeconds $timeout
    $ownership = Get-ResourceOwnership -Resource $resource -Expected $Expected
    if ($ownership -eq "Absent") { return "AlreadyAbsent" }
    if ($ownership -ne "Owned") { return "Refused:$ownership" }
    if ($resource.Id -ne $Id) { return "Refused:IdMismatch" }
    $terminationProblem = $false
    if ($Kind -eq "container") {
        $timeout = Get-CleanupStepTimeout -Ctx $Ctx -Deadline $Deadline
        if ($timeout -le 0) { return "Incomplete" }
        $r = Invoke-DockerCommand -Ctx $Ctx -Arguments @("container", "rm", "-f", $Id) -TimeoutSeconds $timeout
        if (-not $r.TerminationConfirmed) { $terminationProblem = $true }
    }
    else {
        for ($attempt = 1; $attempt -le 3; $attempt++) {
            $timeout = Get-CleanupStepTimeout -Ctx $Ctx -Deadline $Deadline
            if ($timeout -le 0) { return "Incomplete" }
            $r = Invoke-DockerCommand -Ctx $Ctx -Arguments @("network", "rm", $Id) -TimeoutSeconds $timeout
            if (-not $r.TerminationConfirmed) { $terminationProblem = $true }
            if ($r.ExitCode -eq 0) { break }
            if ((Get-CleanupStepTimeout -Ctx $Ctx -Deadline $Deadline) -gt 2) { Start-Sleep -Seconds 2 }
        }
    }
    $timeout = Get-CleanupStepTimeout -Ctx $Ctx -Deadline $Deadline
    if ($timeout -le 0) { return "Incomplete" }
    $after = Get-DockerResource -Ctx $Ctx -Kind $Kind -Ref $Id -TimeoutSeconds $timeout
    if ($terminationProblem) { return "Failed:TerminationUnconfirmed" }
    if ($after.State -eq "Absent") { return "Removed" }
    return "Failed"
}

function Get-CleanupDeadline {
    param([Parameter(Mandatory = $true)]$Ctx)
    return (Get-CleanupClock).AddSeconds([int]$Ctx.Timeouts.CleanupTimeoutSeconds)
}

function Resolve-CleanupFinalState {
    <#
    Estado final de la limpieza (AUD-011):
      NO_RESOURCES_CREATED  no hubo recursos propios ni ambiguos que limpiar.
      CLEANUP_COMPLETED     había recursos y se eliminaron todos.
      CLEANUP_INCOMPLETE    se agotó CleanupTimeoutSeconds antes de terminar.
      CLEANUP_FAILED        una operación falló o se negó a borrar.
    #>
    param([bool]$Required, $Outcome)
    if (-not $Required) { return "NO_RESOURCES_CREATED" }
    if ($Outcome.Ok) { return "CLEANUP_COMPLETED" }
    if ($Outcome.Incomplete -and -not $Outcome.Failed) { return "CLEANUP_INCOMPLETE" }
    return "CLEANUP_FAILED"
}

function Find-PreexistingResources {
    param([Parameter(Mandatory = $true)]$Ctx, [Parameter(Mandatory = $true)]$Names)
    $found = @()
    $container = Get-DockerResource -Ctx $Ctx -Kind container -Ref $Names.ContainerName
    if ($container.State -ne "Absent") { $found += "contenedor $($Names.ContainerName) ($($container.State))" }
    if (Test-CtxWithRedis -Ctx $Ctx) {
        $redis = Get-DockerResource -Ctx $Ctx -Kind container -Ref $Names.RedisContainerName
        if ($redis.State -ne "Absent") { $found += "contenedor $($Names.RedisContainerName) ($($redis.State))" }
    }
    $network = Get-DockerResource -Ctx $Ctx -Kind network -Ref $Names.NetworkName
    if ($network.State -ne "Absent") { $found += "red $($Names.NetworkName) ($($network.State))" }
    $filter = "label=agrotech.run-id=$($Names.RunId)"
    $cl = Invoke-DockerCommand -Ctx $Ctx -Arguments @("container", "ls", "-a", "-q", "--filter", $filter)
    if ($cl.ExitCode -ne 0 -or $cl.TimedOut) { $found += "no se pudo listar contenedores por etiqueta" }
    elseif ($cl.Stdout.Trim()) { $found += "contenedores con la etiqueta del RunId" }
    $nl = Invoke-DockerCommand -Ctx $Ctx -Arguments @("network", "ls", "-q", "--filter", $filter)
    if ($nl.ExitCode -ne 0 -or $nl.TimedOut) { $found += "no se pudo listar redes por etiqueta" }
    elseif ($nl.Stdout.Trim()) { $found += "redes con la etiqueta del RunId" }
    return $found
}

# Únicos endpoints aceptados (AUD-005): named pipes LOCALES ("." como servidor)
# de Docker Desktop en Windows. Se rechazan pipes remotos (npipe:////host/...),
# tcp://, ssh://, unix:// y cualquier otra forma.
$script:AllowedDockerPipeNames = @("docker_engine", "dockerDesktopLinuxEngine", "dockerDesktopWindowsEngine")

function Test-LocalDockerEndpoint {
    param([AllowEmptyString()][AllowNull()][string]$Endpoint)
    if ([string]::IsNullOrWhiteSpace($Endpoint)) { return $false }
    if ($Endpoint -ne $Endpoint.Trim()) { return $false }
    $match = [regex]::Match($Endpoint, '^npipe:////\./pipe/(?<name>[A-Za-z0-9_]+)$')
    if (-not $match.Success) { return $false }
    return ($script:AllowedDockerPipeNames -contains $match.Groups['name'].Value)
}

function Resolve-DockerContext {
    <#
    Exige un contexto Docker cuyo endpoint efectivo sea un named pipe local de
    Docker Desktop y lo fija (--context) para toda la ejecución. No confía en
    el nombre del contexto: valida la dirección que usará el CLI.
    #>
    param([Parameter(Mandatory = $true)]$Ctx)
    $show = Invoke-DockerCommand -Ctx $Ctx -Arguments @("context", "show") -NoContext
    if ($show.ExitCode -ne 0 -or $show.TimedOut) { throw "No se pudo determinar el contexto Docker." }
    $name = $show.Stdout.Trim()
    if ($name -notmatch '^[A-Za-z0-9][A-Za-z0-9_.-]*$') { throw "Nombre de contexto Docker inválido." }
    $inspect = Invoke-DockerCommand -Ctx $Ctx -Arguments @("context", "inspect", $name, "--format", "{{.Endpoints.docker.Host}}") -NoContext
    if ($inspect.ExitCode -ne 0 -or $inspect.TimedOut) { throw "No se pudo inspeccionar el contexto Docker." }
    $endpoint = ([string]$inspect.Stdout).Trim()
    if (-not (Test-LocalDockerEndpoint -Endpoint $endpoint)) {
        throw "El contexto Docker '$name' no usa un named pipe local de Docker Desktop."
    }
    return $name
}

function New-ComposeOverride {
    <#
    Override generado fuera del repositorio: fija la imagen por digest
    (R-ORQ-16) y añade las etiquetas de propiedad de la invocación (AUD-014).
    #>
    param(
        [Parameter(Mandatory = $true)][string]$InvocationId,
        [Parameter(Mandatory = $true)][string]$OwnerProof,
        [Parameter(Mandatory = $true)][string]$ImageReference,
        [string]$RedisImageReference
    )
    $redis = ""
    if (-not [string]::IsNullOrEmpty($RedisImageReference)) {
        $redis = @"

  redis:
    image: "$RedisImageReference"
    labels:
      agrotech.invocation-id: "$InvocationId"
      agrotech.owner-proof: "$OwnerProof"
"@
    }
    return @"
services:
  mariadb:
    image: "$ImageReference"
    labels:
      agrotech.invocation-id: "$InvocationId"
      agrotech.owner-proof: "$OwnerProof"$redis
networks:
  disposable:
    labels:
      agrotech.invocation-id: "$InvocationId"
      agrotech.owner-proof: "$OwnerProof"
"@
}

function Get-ComposeBaseArguments {
    <#
    Argumentos deterministas de Compose (AUD-016): proyecto, directorio de
    proyecto (fuera del repositorio), archivo de entorno vacío y exactamente
    dos archivos: el compose del repositorio y el override de la invocación.
    #>
    param([Parameter(Mandatory = $true)]$Ctx)
    # Con -WithRedis (F10.3.4-A) se añade docker-compose.disposable.redis.yml
    # del repositorio; el override de la invocación sigue siendo el último -f.
    $files = @("-f", $Ctx.ComposeFile)
    if (Test-CtxWithRedis -Ctx $Ctx) { $files += @("-f", $Ctx.RedisComposeFile) }
    $files += @("-f", (Join-Path $Ctx.ComposeDirectory "invocation.override.yml"))
    return @(
        "compose", "--project-directory", $Ctx.ComposeDirectory,
        "--env-file", (Join-Path $Ctx.ComposeDirectory "empty.env")
    ) + $files + @("-p", $Ctx.Names.ProjectName)
}

function Test-ComposeDirectoryClean {
    <#
    El directorio de proyecto de Compose solo puede contener el override y el
    archivo de entorno vacío generados por esta invocación: ni .env ni otros
    compose/override que Compose pudiera cargar (AUD-016).
    #>
    param([Parameter(Mandatory = $true)]$Ctx)
    try { Assert-DirectoryInUse -Path $Ctx.ComposeDirectory -Root $Ctx.Root } catch { return $_.Exception.Message }
    $names = @(Get-ChildItem -LiteralPath $Ctx.ComposeDirectory -Force | ForEach-Object { $_.Name } | Sort-Object)
    if (($names -join "|") -cne "empty.env|invocation.override.yml") {
        return "el directorio de Compose contiene archivos no autorizados: " + ($names -join ", ")
    }
    $emptyEnv = Get-Item -LiteralPath (Join-Path $Ctx.ComposeDirectory "empty.env") -Force
    if ($emptyEnv.Length -ne 0) { return "el archivo de entorno de Compose no está vacío" }
    return $null
}

function Test-PinnedImageAvailable {
    <#
    La imagen fijada debe existir ya en local con el digest exigido; no se
    descarga nunca (compose up --pull never). Sin ella se aborta (R-ORQ-16).
    #>
    param([Parameter(Mandatory = $true)]$Ctx)
    return (Test-LocalImageDigest -Ctx $Ctx -Repository $script:ImageRepository -Tag $script:ImageTag -Reference $Ctx.ImageReference)
}

function Test-PinnedRedisImageAvailable {
    <# Igual que Test-PinnedImageAvailable para redis:7.4.12 (F10.3.4-A). #>
    param([Parameter(Mandatory = $true)]$Ctx)
    return (Test-LocalImageDigest -Ctx $Ctx -Repository $script:RedisImageRepository -Tag $script:RedisImageTag -Reference $Ctx.RedisImageReference)
}

function Test-LocalImageDigest {
    param(
        [Parameter(Mandatory = $true)]$Ctx,
        [Parameter(Mandatory = $true)][string]$Repository,
        [Parameter(Mandatory = $true)][string]$Tag,
        [Parameter(Mandatory = $true)][string]$Reference
    )
    $digest = ($Reference -split '@', 2)[1]
    $r = Invoke-DockerCommand -Ctx $Ctx -Arguments @("image", "ls", "--no-trunc", "--digests", "--filter", ("reference={0}:{1}" -f $Repository, $Tag), "--format", "{{.Repository}}|{{.Tag}}|{{.Digest}}")
    if ($r.StartError -or $r.TimedOut -or $r.ExitCode -ne 0) { return "no se pudo comprobar la imagen local (" + (Get-DockerErrorCategory -Stderr $r.Stderr) + ")" }
    $expected = "{0}|{1}|{2}" -f $Repository, $Tag, $digest
    foreach ($line in ([string]$r.Stdout -split "`r?`n")) {
        if ($line.Trim() -ceq $expected) { return $null }
    }
    return "la imagen $Reference no está disponible localmente con ese digest; no se descarga"
}

function Test-ComposeRenderedConfig {
    <#
    Valida la configuración que Compose renderiza (compose config --format
    json) antes de crear nada (AUD-016): proyecto, único servicio, imagen
    fijada, nombres, etiquetas de propiedad, puerto en loopback y ausencia de
    volúmenes, bind mounts, env_file, build y modos privilegiados.
    #>
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Json, [Parameter(Mandatory = $true)]$Ctx)
    try { $config = ConvertFrom-Json -InputObject $Json } catch { return "configuración de Compose no interpretable" }
    if ($null -eq $config) { return "configuración de Compose vacía" }
    if ([string](Get-JsonValue -Object $config -Path @("name")) -cne $Ctx.Names.ProjectName) { return "proyecto Compose distinto del esperado" }
    $services = Get-JsonValue -Object $config -Path @("services")
    if ($null -eq $services) { return "sin servicios" }
    $serviceNames = @($services.PSObject.Properties | ForEach-Object { $_.Name } | Sort-Object)
    $expectedServices = "mariadb"
    if (Test-CtxWithRedis -Ctx $Ctx) { $expectedServices = "mariadb,redis" }
    if (($serviceNames -join ",") -cne $expectedServices) { return "servicios inesperados: " + ($serviceNames -join ",") }
    $svc = $services.mariadb
    if ([string](Get-JsonValue -Object $svc -Path @("image")) -cne $Ctx.ImageReference) { return "imagen distinta de la fijada" }
    if ([string](Get-JsonValue -Object $svc -Path @("container_name")) -cne $Ctx.Names.ContainerName) { return "container_name inesperado" }
    if ([string](Get-JsonValue -Object $svc -Path @("hostname")) -cne $Ctx.Names.ContainerHostname) { return "hostname inesperado" }
    foreach ($forbidden in @("build", "env_file", "volumes", "volumes_from", "network_mode", "pid", "ipc", "devices", "cap_add", "extends")) {
        $value = Get-JsonValue -Object $svc -Path @($forbidden)
        if ($null -ne $value -and @($value).Count -gt 0) { return "opción no permitida en el servicio: $forbidden" }
    }
    if ((Get-JsonValue -Object $svc -Path @("privileged")) -eq $true) { return "servicio privilegiado" }
    $expectedLabels = @{
        "agrotech.disposable" = "true"; "agrotech.run-id" = $Ctx.Names.RunId; "agrotech.purpose" = $script:LabelPurpose
        "agrotech.invocation-id" = $Ctx.InvocationId; "agrotech.owner-proof" = $Ctx.OwnerProof
    }
    $svcLabels = ConvertTo-LabelTable -Labels (Get-JsonValue -Object $svc -Path @("labels"))
    foreach ($key in $expectedLabels.Keys) {
        if (-not $svcLabels.ContainsKey($key) -or $svcLabels[$key] -cne $expectedLabels[$key]) { return "etiqueta de servicio faltante o distinta: $key" }
    }
    $ports = @(Get-JsonValue -Object $svc -Path @("ports"))
    if ($ports.Count -ne 1) { return "se esperaba exactamente una publicación de puerto" }
    $port = $ports[0]
    if ([string](Get-JsonValue -Object $port -Path @("host_ip")) -cne "127.0.0.1" -or [string](Get-JsonValue -Object $port -Path @("published")) -cne [string]$script:ContainerPort -or [string](Get-JsonValue -Object $port -Path @("target")) -cne "3306") {
        return "publicación de puerto distinta de 127.0.0.1:$($script:ContainerPort)->3306"
    }
    $svcNetworks = Get-JsonValue -Object $svc -Path @("networks")
    $svcNetworkNames = @()
    if ($null -ne $svcNetworks) { $svcNetworkNames = @($svcNetworks.PSObject.Properties | ForEach-Object { $_.Name }) }
    if (($svcNetworkNames -join ",") -cne "disposable") { return "redes del servicio inesperadas" }
    $networks = Get-JsonValue -Object $config -Path @("networks")
    $networkNames = @()
    if ($null -ne $networks) { $networkNames = @($networks.PSObject.Properties | ForEach-Object { $_.Name }) }
    if (($networkNames -join ",") -cne "disposable") { return "redes del proyecto inesperadas" }
    $net = $networks.disposable
    if ([string](Get-JsonValue -Object $net -Path @("name")) -cne $Ctx.Names.NetworkName) { return "nombre de red inesperado" }
    if ((Get-JsonValue -Object $net -Path @("external")) -eq $true) { return "red externa no permitida" }
    $netLabels = ConvertTo-LabelTable -Labels (Get-JsonValue -Object $net -Path @("labels"))
    foreach ($key in $expectedLabels.Keys) {
        if (-not $netLabels.ContainsKey($key) -or $netLabels[$key] -cne $expectedLabels[$key]) { return "etiqueta de red faltante o distinta: $key" }
    }
    $topVolumes = Get-JsonValue -Object $config -Path @("volumes")
    if ($null -ne $topVolumes -and @($topVolumes.PSObject.Properties).Count -gt 0) { return "volúmenes de proyecto no permitidos" }
    if (Test-CtxWithRedis -Ctx $Ctx) {
        $redisProblem = Test-RedisRenderedService -Service $services.redis -Ctx $Ctx -ExpectedLabels $expectedLabels
        if ($redisProblem) { return "Redis: " + $redisProblem }
    }
    return $null
}

function Test-RedisRenderedService {
    <#
    Servicio redis renderizado (F10.3.4-A): imagen fijada, nombres, etiquetas
    de propiedad, una sola publicación 127.0.0.1:16379->6379, solo la red del
    proyecto, datos en tmpfs y las mismas opciones prohibidas que MariaDB.
    #>
    param($Service, [Parameter(Mandatory = $true)]$Ctx, [Parameter(Mandatory = $true)][hashtable]$ExpectedLabels)
    if ($null -eq $Service) { return "servicio ausente" }
    if ([string](Get-JsonValue -Object $Service -Path @("image")) -cne $Ctx.RedisImageReference) { return "imagen distinta de la fijada" }
    if ([string](Get-JsonValue -Object $Service -Path @("container_name")) -cne $Ctx.Names.RedisContainerName) { return "container_name inesperado" }
    if ([string](Get-JsonValue -Object $Service -Path @("hostname")) -cne $Ctx.Names.RedisContainerHostname) { return "hostname inesperado" }
    foreach ($forbidden in @("build", "env_file", "volumes", "volumes_from", "network_mode", "pid", "ipc", "devices", "cap_add", "extends")) {
        $value = Get-JsonValue -Object $Service -Path @($forbidden)
        if ($null -ne $value -and @($value).Count -gt 0) { return "opción no permitida en el servicio: $forbidden" }
    }
    if ((Get-JsonValue -Object $Service -Path @("privileged")) -eq $true) { return "servicio privilegiado" }
    $labels = ConvertTo-LabelTable -Labels (Get-JsonValue -Object $Service -Path @("labels"))
    foreach ($key in $ExpectedLabels.Keys) {
        if (-not $labels.ContainsKey($key) -or $labels[$key] -cne $ExpectedLabels[$key]) { return "etiqueta de servicio faltante o distinta: $key" }
    }
    $ports = @(Get-JsonValue -Object $Service -Path @("ports"))
    if ($ports.Count -ne 1) { return "se esperaba exactamente una publicación de puerto" }
    $port = $ports[0]
    if ([string](Get-JsonValue -Object $port -Path @("host_ip")) -cne "127.0.0.1" -or [string](Get-JsonValue -Object $port -Path @("published")) -cne [string]$script:RedisPort -or [string](Get-JsonValue -Object $port -Path @("target")) -cne [string]$script:RedisContainerPort) {
        return "publicación de puerto distinta de 127.0.0.1:$($script:RedisPort)->$($script:RedisContainerPort)"
    }
    $networks = Get-JsonValue -Object $Service -Path @("networks")
    $networkNames = @()
    if ($null -ne $networks) { $networkNames = @($networks.PSObject.Properties | ForEach-Object { $_.Name }) }
    if (($networkNames -join ",") -cne "disposable") { return "redes del servicio inesperadas" }
    $tmpfs = @(Get-JsonValue -Object $Service -Path @("tmpfs") | ForEach-Object { [string]$_ })
    if (@($tmpfs | Where-Object { $_ -like "/data:*" -or $_ -ceq "/data" }).Count -ne 1) { return "los datos de Redis no están en tmpfs" }
    return $null
}

function ConvertFrom-GrantSchemaTarget {
    <#
    Decodifica de forma estricta el destino `esquema`.* o `esquema`.`tabla` de
    SHOW GRANTS (mismas reglas que config.disposable_preflight.decode_grant_schema).
    Acepta \_ y \% (escape de MariaDB) y \\_ (escape duplicado de mariadb
    --batch sin --raw). Rechaza comodines sin escapar (_ y %), escapes
    desconocidos y formatos no reconocidos. Devuelve Ok, Schema y Reason.
    #>
    param([AllowEmptyString()][string]$Target)
    $match = [regex]::Match($Target, '^`(?<schema>[^`]+)`\.(?:\*|`[^`]+`)$')
    if (-not $match.Success) { return [pscustomobject]@{ Ok = $false; Schema = $null; Reason = "destino de concesión no reconocido" } }
    $raw = $match.Groups['schema'].Value.Replace('\\', '\')
    $builder = New-Object System.Text.StringBuilder
    $index = 0
    while ($index -lt $raw.Length) {
        $ch = $raw[$index]
        if ($ch -eq [char]'\') {
            $next = $null
            if ($index + 1 -lt $raw.Length) { $next = $raw[$index + 1] }
            if ($next -eq [char]'_' -or $next -eq [char]'%') {
                [void]$builder.Append($next)
                $index += 2
                continue
            }
            return [pscustomobject]@{ Ok = $false; Schema = $null; Reason = "escape no reconocido en el destino de concesión" }
        }
        if ($ch -eq [char]'_' -or $ch -eq [char]'%') {
            return [pscustomobject]@{ Ok = $false; Schema = $null; Reason = "comodín sin escapar en el destino de concesión" }
        }
        [void]$builder.Append($ch)
        $index++
    }
    return [pscustomobject]@{ Ok = $true; Schema = $builder.ToString(); Reason = $null }
}

function Test-GrantOutput {
    <#
    Valida la salida de SHOW GRANTS (en el contenedor) sin registrarla: puede
    contener el hash de la contraseña. Mismas reglas que
    config.disposable_preflight.check_privileges.
    #>
    param([string[]]$Lines, [Parameter(Mandatory = $true)][string]$Database)
    $required = @("SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "INDEX", "DROP", "REFERENCES")
    $granted = @{}
    $roleSeen = $false
    $rolesSeen = $false
    foreach ($raw in @($Lines)) {
        $line = ([string]$raw).Trim()
        if (-not $line) { continue }
        if ($line -like "ROLE:*") {
            $roleSeen = $true
            if ($line -ne "ROLE:NONE") { return "rol activo" }
            continue
        }
        if ($line -like "ROLES:*") {
            $rolesSeen = $true
            if ($line -ne "ROLES:0") { return "roles aplicables" }
            continue
        }
        $grantMatch = [regex]::Match($line, '^GRANT\s+(?<privs>.+?)\s+ON\s+(?<target>\S+)\s+TO\s+(?<rest>.+)$', [System.Text.RegularExpressions.RegexOptions]::IgnoreCase)
        if (-not $grantMatch.Success) {
            return "concesión no reconocida"
        }
        $privs = $grantMatch.Groups['privs'].Value.ToUpperInvariant()
        $target = $grantMatch.Groups['target'].Value
        $rest = $grantMatch.Groups['rest'].Value.ToUpperInvariant()
        if ($rest.Contains("WITH GRANT OPTION")) { return "GRANT OPTION" }
        if ($privs.StartsWith("PROXY")) { return "PROXY" }
        if ($target -eq "*.*") {
            if ($privs -ne "USAGE") { return "privilegios globales" }
            continue
        }
        $decoded = ConvertFrom-GrantSchemaTarget -Target $target
        if (-not $decoded.Ok) { return $decoded.Reason }
        if ($decoded.Schema -cne $Database) { return "privilegios fuera de la base desechable" }
        if (-not $target.EndsWith(".*")) { continue }
        if ($privs -eq "ALL" -or $privs -eq "ALL PRIVILEGES") {
            foreach ($p in $required) { $granted[$p] = $true }
        }
        else {
            foreach ($p in ($privs -split ',')) { $granted[$p.Trim()] = $true }
        }
    }
    if (-not $roleSeen -or -not $rolesSeen) { return "no se pudo verificar roles" }
    foreach ($p in $required) {
        if (-not $granted.ContainsKey($p)) { return "faltan privilegios mínimos" }
    }
    return $null
}

function Test-LoopbackPortFree {
    param([int]$Port = $script:ContainerPort)
    $listeners = [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners()
    foreach ($listener in $listeners) {
        if ($listener.Port -eq $Port) { return $false }
    }
    return $true
}

$script:OrchestratorMutex = $null
# Global\: el puerto 127.0.0.1:3317 es de toda la máquina; una sola ejecución
# entre todas las sesiones y usuarios (AUD-014). Si otro usuario creó el mutex
# y no hay permiso para abrirlo, se trata como ocupado (falla cerrado).
$script:OrchestratorMutexName = "Global\AgrotechDisposableOrchestrator"

function Enter-OrchestratorLock {
    try {
        $mutex = New-Object System.Threading.Mutex($false, $script:OrchestratorMutexName)
    }
    catch {
        return $false
    }
    $acquired = $false
    try {
        $acquired = $mutex.WaitOne(0)
    }
    catch [System.Threading.AbandonedMutexException] {
        $acquired = $true
    }
    if (-not $acquired) {
        $mutex.Dispose()
        return $false
    }
    $script:OrchestratorMutex = $mutex
    return $true
}

function Exit-OrchestratorLock {
    if ($null -ne $script:OrchestratorMutex) {
        try { $script:OrchestratorMutex.ReleaseMutex() } catch { }
        $script:OrchestratorMutex.Dispose()
        $script:OrchestratorMutex = $null
    }
}

# ---------------------------------------------------------------------------
# Python (todas las llamadas pasan por Invoke-PythonChild)
# ---------------------------------------------------------------------------

function Invoke-PythonChild {
    param(
        [Parameter(Mandatory = $true)]$Ctx,
        [Parameter(Mandatory = $true)][string]$Step,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][int]$TimeoutSeconds
    )
    $full = @("-B", "-E", "-s", "-X", "utf8") + $Arguments
    $result = Invoke-BoundedProcess -FilePath $Ctx.Python -ArgumentList $full -TimeoutSeconds $TimeoutSeconds -Environment $Ctx.PythonEnvironment -WorkingDirectory $Ctx.Root -Redact $Ctx.Secrets
    Write-StepLog -Ctx $Ctx -Step ("python-" + $Step) -Result $result
    Register-ProcessTermination -Ctx $Ctx -Step ("python " + $Step) -Result $result
    return $result
}

function Get-PreflightArguments {
    param([Parameter(Mandatory = $true)]$Ctx, [Parameter(Mandatory = $true)][string]$Command, [string]$ExpectSchema = "empty")
    return @("-m", "config.disposable_preflight", $Command, "--settings-module", $Ctx.SettingsModule, "--run-id", $Ctx.Names.RunId, "--expect-schema", $ExpectSchema)
}

function Get-PytestArguments {
    param([Parameter(Mandatory = $true)]$Ctx)
    $list = @(
        "-m", "pytest",
        "-p", "config.disposable_preflight",
        "-p", "no:cacheprovider",
        "-o", "addopts=",
        "--ds=$($Ctx.SettingsModule)",
        "--reuse-db",
        "--junitxml=$(Join-Path $Ctx.OutputDirectory 'junit.xml')"
    )
    return $list + @($Ctx.Tests)
}

function ConvertTo-StageCode {
    <# Traduce el resultado de un hijo Python a la tabla central. #>
    param([Parameter(Mandatory = $true)]$Result, [ValidateSet("preflight", "pytest")][string]$Kind = "preflight")
    if ($Result.StartError -or $Result.TimedOut) { return (Get-DisposableExitCode INFRASTRUCTURE_FAILED) }
    # Un descendiente vivo impide declarar éxito aunque el código sea 0 (AUD-008).
    if (-not $Result.TerminationConfirmed) { return (Get-DisposableExitCode INFRASTRUCTURE_FAILED) }
    if ($Kind -eq "pytest") {
        if ($Result.ExitCode -eq 0) { return 0 }
        return (Get-DisposableExitCode TESTS_FAILED)
    }
    switch ([int]$Result.ExitCode) {
        0 { return 0 }
        10 { return (Get-DisposableExitCode PREFLIGHT_REJECTED) }
        20 { return (Get-DisposableExitCode MIGRATION_FAILED) }
        default { return (Get-DisposableExitCode INFRASTRUCTURE_FAILED) }
    }
}

function Get-FinalExitCode {
    param(
        [int]$StageCode,
        [bool]$CleanupRequired,
        [bool]$CleanupOk,
        [bool]$Interrupted
    )
    if ($Interrupted) { return (Get-DisposableExitCode INTERRUPTED) }
    if ($CleanupRequired -and -not $CleanupOk) {
        if ($StageCode -eq 0) { return (Get-DisposableExitCode CLEANUP_FAILED) }
        return (Get-DisposableExitCode STAGE_AND_CLEANUP_FAILED)
    }
    return $StageCode
}

function Get-FinalMessage {
    param([int]$Code, [string]$Mode)
    switch ($Code) {
        0 {
            switch ($Mode) {
                "Validate" { return "Entorno desechable validado y eliminado." }
                "SqlMigrateOnly" { return "sqlmigrate ejecutado tras preflight; entorno eliminado." }
                "CleanupOnly" { return "Limpieza completada: solo se eliminaron recursos con propiedad demostrada." }
                default { return "Preflight correcto; entorno eliminado. No se ejecutaron migraciones ni pruebas." }
            }
        }
        3 { return "Parámetros o combinación no permitida. No se crearon recursos." }
        10 { return "Preflight rechazado. No se ejecutaron migraciones ni pruebas." }
        20 { return "Falló la migración en la base desechable." }
        30 { return "Fallaron las pruebas en la base desechable." }
        40 { return "Fallo de infraestructura o timeout." }
        41 { return "La etapa terminó, pero la limpieza falló: revise el estado y recupere con -CleanupOnly." }
        42 { return "Fallaron la etapa y la limpieza: revise el estado y recupere con -CleanupOnly." }
        43 { return "Ejecución interrumpida: estado indeterminado; revise run-state.json." }
        50 { return "Recurso preexistente o propiedad no demostrada. No se eliminó ningún recurso ajeno." }
        default { return "Resultado desconocido." }
    }
}

# ---------------------------------------------------------------------------
# Flujo principal
# ---------------------------------------------------------------------------

function New-OrchestratorContext {
    param($Normalized, [string]$Root, [string]$RunId, [string]$InvocationId, [string]$OutputDirectory)
    $names = Get-DisposableNames -Id $RunId
    $ctx = @{
        Root = $Root; Mode = $Normalized.Mode; SettingsModule = $Normalized.SettingsModule
        Tests = @($Normalized.Tests); Timeouts = $Normalized.Timeouts; Names = $names
        InvocationId = $InvocationId; OutputDirectory = $OutputDirectory
        LogDirectory = (Join-Path $OutputDirectory "logs")
        ComposeDirectory = (Join-Path $OutputDirectory "compose")
        StateFile = (Join-Path $OutputDirectory "run-state.json")
        ComposeFile = (Join-Path $Root "docker-compose.disposable.yml")
        Python = (Join-Path $Root ".venv\Scripts\python.exe")
        DockerExe = $null; DockerContext = $null
        DockerEnvironment = (Get-BaseChildEnvironment); PythonEnvironment = @{}
        Secrets = @(); Owned = @{ ContainerId = $null; NetworkId = $null; Volumes = @(); RedisContainerId = $null }
        OwnerToken = $null; OwnerProof = ""; ProtectedToken = $null
        ImageReference = $Normalized.ImageReference
        WithRedis = [bool](Get-JsonValue -Object $Normalized -Path @("WithRedis"))
        RedisImageReference = Get-JsonValue -Object $Normalized -Path @("RedisImageReference")
        RedisComposeFile = (Join-Path $Root $script:RedisComposeFileName)
        OwnershipProblems = New-Object System.Collections.Generic.List[string]
        TerminationFailures = New-Object System.Collections.Generic.List[object]; Phase = "stage"
        State = "NOT_STARTED"; StageCode = 0; StageMessage = ""; Cleanup = $null; ExitCode = $null
        History = New-Object System.Collections.Generic.List[object]; LogSequence = 0
    }
    return $ctx
}

function Set-StageFailure {
    param($Ctx, [Parameter(Mandatory = $true)][string]$CodeName, [string]$Message)
    if ($Ctx.StageCode -eq 0) {
        $Ctx.StageCode = Get-DisposableExitCode $CodeName
        $Ctx.StageMessage = $Message
    }
    Set-RunState -Ctx $Ctx -State "FAILED" -Detail $Message
}

function Update-Ownership {
    <# Registra la propiedad tras compose up, haya terminado bien o no. #>
    param($Ctx)
    $expected = Get-ExpectedOwnership -Names $Ctx.Names -InvocationId $Ctx.InvocationId -OwnerProof $Ctx.OwnerProof
    foreach ($kind in @("container", "network")) {
        $name = $Ctx.Names.NetworkName
        if ($kind -eq "container") { $name = $Ctx.Names.ContainerName }
        $resource = Get-DockerResource -Ctx $Ctx -Kind $kind -Ref $name
        $ownership = Get-ResourceOwnership -Resource $resource -Expected $expected
        if ($ownership -eq "Owned") {
            if ($kind -eq "container") {
                $Ctx.Owned.ContainerId = $resource.Id
                # R-ORQ-8: se registran los volúmenes anónimos montados en el
                # contenedor propio; los nombrados se informan y nunca se borran.
                $volumes = Get-OwnedVolumeCandidates -Resource $resource
                $Ctx.Owned.Volumes = @($volumes.Anonymous)
                foreach ($named in @($volumes.Named)) {
                    $Ctx.OwnershipProblems.Add("volumen nombrado montado en el contenedor (no se elimina): $named")
                }
            }
            else { $Ctx.Owned.NetworkId = $resource.Id }
        }
        elseif ($ownership -ne "Absent") {
            $Ctx.OwnershipProblems.Add("$kind $name : $ownership")
        }
    }
    if (Test-CtxWithRedis -Ctx $Ctx) {
        $redisExpected = Get-ExpectedRedisOwnership -Names $Ctx.Names -InvocationId $Ctx.InvocationId -OwnerProof $Ctx.OwnerProof
        $name = $Ctx.Names.RedisContainerName
        $resource = Get-DockerResource -Ctx $Ctx -Kind container -Ref $name
        $ownership = Get-ResourceOwnership -Resource $resource -Expected $redisExpected
        if ($ownership -eq "Owned") {
            $Ctx.Owned.RedisContainerId = $resource.Id
            $volumes = Get-OwnedVolumeCandidates -Resource $resource
            $Ctx.Owned.Volumes = @(@($Ctx.Owned.Volumes) + @($volumes.Anonymous) | Where-Object { $_ })
            foreach ($named in @($volumes.Named)) {
                $Ctx.OwnershipProblems.Add("volumen nombrado montado en el contenedor Redis (no se elimina): $named")
            }
        }
        elseif ($ownership -ne "Absent") {
            $Ctx.OwnershipProblems.Add("container $name : $ownership")
        }
    }
    Save-RunState -Ctx $Ctx
}

function Invoke-OwnedVolumeCleanup {
    <#
    Volúmenes anónimos registrados (R-ORQ-8), solo después de eliminar el
    contenedor. Si el contenedor no se eliminó, se conservan y se informan.
    #>
    param($Ctx, $Outcome, $Expected, [Nullable[datetime]]$Deadline, [string[]]$Volumes, [bool]$ContainerGone)
    $results = @()
    foreach ($volume in @($Volumes | Where-Object { $_ })) {
        $result = "Kept:ContainerNotRemoved"
        if ($ContainerGone) { $result = Remove-OwnedVolume -Ctx $Ctx -Name $volume -Expected $Expected -Deadline $Deadline }
        $results += ("{0}={1}" -f $volume, $result)
        Add-CleanupOutcome -Outcome $Outcome -Result $result
    }
    $Outcome.Volumes = $results
}

function Add-CleanupOutcome {
    <# Acumula el resultado de borrar un recurso en el resumen de limpieza. #>
    param($Outcome, [string]$Result)
    if ($Result -in @("Removed", "AlreadyAbsent", "NotOwned")) { return }
    $Outcome.Ok = $false
    if ($Result -eq "Incomplete") { $Outcome.Incomplete = $true } else { $Outcome.Failed = $true }
}

function Invoke-OwnedCleanup {
    <#
    Limpia solo lo propio dentro del plazo global CleanupTimeoutSeconds
    (AUD-007). Si el plazo se agota, los recursos restantes NO se borran y el
    resultado queda Incomplete; la recuperación es -CleanupOnly.
    #>
    param($Ctx)
    $expected = Get-ExpectedOwnership -Names $Ctx.Names -InvocationId $Ctx.InvocationId -OwnerProof $Ctx.OwnerProof
    $deadline = Get-CleanupDeadline -Ctx $Ctx
    $outcome = @{ Container = "NotOwned"; Network = "NotOwned"; Volumes = @(); Ok = $true; Incomplete = $false; Failed = $false; Problems = $Ctx.OwnershipProblems.ToArray() }
    if ($Ctx.OwnershipProblems.Count -gt 0) { $outcome.Ok = $false; $outcome.Failed = $true }
    $redisGone = $true
    if (Test-CtxWithRedis -Ctx $Ctx) {
        $outcome.Redis = "NotOwned"
        $redisId = Get-CtxRedisContainerId -Ctx $Ctx
        if ($redisId) {
            $redisExpected = Get-ExpectedRedisOwnership -Names $Ctx.Names -InvocationId $Ctx.InvocationId -OwnerProof $Ctx.OwnerProof
            $outcome.Redis = Remove-OwnedResource -Ctx $Ctx -Kind container -Id $redisId -Expected $redisExpected -Deadline $deadline
            Add-CleanupOutcome -Outcome $outcome -Result $outcome.Redis
            $redisGone = ($outcome.Redis -in @("Removed", "AlreadyAbsent"))
        }
    }
    if ($Ctx.Owned.ContainerId) {
        $outcome.Container = Remove-OwnedResource -Ctx $Ctx -Kind container -Id $Ctx.Owned.ContainerId -Expected $expected -Deadline $deadline
        Add-CleanupOutcome -Outcome $outcome -Result $outcome.Container
    }
    # Un volumen solo se borra cuando ningún contenedor propio puede seguir usándolo.
    Invoke-OwnedVolumeCleanup -Ctx $Ctx -Outcome $outcome -Expected $expected -Deadline $deadline -Volumes @($Ctx.Owned.Volumes) -ContainerGone (($outcome.Container -in @("Removed", "AlreadyAbsent")) -and $redisGone)
    if ($Ctx.Owned.NetworkId) {
        $outcome.Network = Remove-OwnedResource -Ctx $Ctx -Kind network -Id $Ctx.Owned.NetworkId -Expected $expected -Deadline $deadline
        Add-CleanupOutcome -Outcome $outcome -Result $outcome.Network
    }
    return $outcome
}

function Wait-ContainerHealthy {
    param($Ctx, [string]$ContainerId)
    if ([string]::IsNullOrEmpty($ContainerId)) { $ContainerId = $Ctx.Owned.ContainerId }
    $deadline = (Get-Date).AddSeconds($Ctx.Timeouts.HealthTimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $r = Invoke-DockerCommand -Ctx $Ctx -Arguments @("container", "inspect", $ContainerId, "--format", "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}")
        if ($r.ExitCode -eq 0 -and $r.Stdout.Trim() -eq "healthy") { return $true }
        Start-Sleep -Seconds 2
    }
    return $false
}

function Test-ContainerIdentity {
    <# Revalida propiedad, hostname, publicación del puerto y privilegios. #>
    param($Ctx)
    $expected = Get-ExpectedOwnership -Names $Ctx.Names -InvocationId $Ctx.InvocationId -OwnerProof $Ctx.OwnerProof
    $resource = Get-DockerResource -Ctx $Ctx -Kind container -Ref $Ctx.Owned.ContainerId
    if ((Get-ResourceOwnership -Resource $resource -Expected $expected) -ne "Owned") { return "propiedad del contenedor no demostrada" }
    $port = Invoke-DockerCommand -Ctx $Ctx -Arguments @("port", $Ctx.Owned.ContainerId, "3306/tcp")
    $bindings = @(([string]$port.Stdout) -split "`r?`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    if ($port.ExitCode -ne 0 -or $bindings.Count -ne 1 -or $bindings[0] -ne "127.0.0.1:$($script:ContainerPort)") {
        return "publicación del puerto distinta de 127.0.0.1:$($script:ContainerPort)"
    }
    $sql = "SHOW GRANTS FOR CURRENT_USER(); SELECT CONCAT('ROLE:', IFNULL(CURRENT_ROLE(), 'NONE')); SELECT CONCAT('ROLES:', COUNT(*)) FROM information_schema.APPLICABLE_ROLES;"
    # --raw evita que --batch duplique las barras invertidas (AUD-003); el
    # decodificador acepta igualmente \\_ por si el cliente no lo respetara.
    $shell = 'MYSQL_PWD="$MARIADB_PASSWORD" exec mariadb -u ' + $script:DbUser + ' -h 127.0.0.1 --skip-column-names --batch --raw -e "' + $sql + '"'
    $grants = Invoke-DockerCommand -Ctx $Ctx -Arguments @("exec", $Ctx.Owned.ContainerId, "sh", "-c", $shell) -NoOutputLog
    if ($grants.ExitCode -ne 0 -or $grants.TimedOut) { return "no se pudieron verificar los privilegios en el contenedor" }
    $problem = Test-GrantOutput -Lines (([string]$grants.Stdout) -split "`r?`n") -Database $Ctx.Names.DatabaseName
    if ($problem) { return "privilegios no permitidos: $problem" }
    return $null
}

function Test-RedisIdentity {
    <#
    Revalida el contenedor Redis (F10.3.4-A): propiedad, publicación única en
    127.0.0.1:16379, autenticación obligatoria (sin contraseña responde
    NOAUTH) y escribe en la base 1 la marca con el RunId que el preflight de
    Python comprueba con la conexión real. Ningún comando lleva la contraseña
    en sus argumentos: redis-cli la toma de REDISCLI_AUTH dentro del contenedor.
    #>
    param($Ctx)
    $redisId = Get-CtxRedisContainerId -Ctx $Ctx
    $expected = Get-ExpectedRedisOwnership -Names $Ctx.Names -InvocationId $Ctx.InvocationId -OwnerProof $Ctx.OwnerProof
    $resource = Get-DockerResource -Ctx $Ctx -Kind container -Ref $redisId
    if ((Get-ResourceOwnership -Resource $resource -Expected $expected) -ne "Owned") { return "propiedad del contenedor Redis no demostrada" }
    $port = Invoke-DockerCommand -Ctx $Ctx -Arguments @("port", $redisId, "$($script:RedisContainerPort)/tcp")
    $bindings = @(([string]$port.Stdout) -split "`r?`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    if ($port.ExitCode -ne 0 -or $bindings.Count -ne 1 -or $bindings[0] -ne "127.0.0.1:$($script:RedisPort)") {
        return "publicación del puerto de Redis distinta de 127.0.0.1:$($script:RedisPort)"
    }
    $anonymous = Invoke-DockerCommand -Ctx $Ctx -Arguments @("exec", $redisId, "sh", "-c", "unset REDISCLI_AUTH; exec redis-cli -h 127.0.0.1 ping")
    if ($anonymous.TimedOut -or (([string]$anonymous.Stdout + [string]$anonymous.Stderr) -notmatch 'NOAUTH')) {
        return "Redis no exige autenticación"
    }
    $marker = Invoke-DockerCommand -Ctx $Ctx -Arguments @("exec", $redisId, "redis-cli", "-h", "127.0.0.1", "-n", [string]$script:RedisMarkerDb, "SET", $script:RedisMarkerKey, $Ctx.Names.RunId)
    if ($marker.ExitCode -ne 0 -or $marker.TimedOut -or ([string]$marker.Stdout).Trim() -cne "OK") {
        return "no se pudo registrar la marca de identidad en Redis"
    }
    return $null
}

function Invoke-EnvironmentStages {
    <# Crea el entorno y ejecuta las etapas del modo. Lanza solo por interrupción. #>
    param($Ctx)
    $timeouts = $Ctx.Timeouts

    $ver = Invoke-DockerCommand -Ctx $Ctx -Arguments @("version", "--format", "{{.Server.Version}}") -NoContext
    if ($ver.ExitCode -ne 0 -or $ver.TimedOut) { Set-StageFailure $Ctx INFRASTRUCTURE_FAILED "Docker no responde."; return }
    try { $Ctx.DockerContext = Resolve-DockerContext -Ctx $Ctx }
    catch { Set-StageFailure $Ctx INFRASTRUCTURE_FAILED $_.Exception.Message; return }
    $cv = Invoke-DockerCommand -Ctx $Ctx -Arguments @("compose", "version")
    if ($cv.ExitCode -ne 0 -or $cv.TimedOut) { Set-StageFailure $Ctx INFRASTRUCTURE_FAILED "Docker Compose no responde."; return }

    $pre = @(Find-PreexistingResources -Ctx $Ctx -Names $Ctx.Names)
    if ($pre.Count -gt 0) {
        Set-StageFailure $Ctx PREEXISTING_OR_OWNERSHIP ("Recursos preexistentes: " + ($pre -join "; "))
        Set-RunState -Ctx $Ctx -State "ABORTED_PREEXISTING" -Detail "no se crea ni se elimina ningún recurso"
        return
    }
    if (-not (Test-LoopbackPortFree)) {
        Set-StageFailure $Ctx PREEXISTING_OR_OWNERSHIP "El puerto 127.0.0.1:$($script:ContainerPort) ya está en uso."
        return
    }
    $withRedis = Test-CtxWithRedis -Ctx $Ctx
    if ($withRedis -and -not (Test-LoopbackPortFree -Port $script:RedisPort)) {
        Set-StageFailure $Ctx PREEXISTING_OR_OWNERSHIP "El puerto 127.0.0.1:$($script:RedisPort) (Redis) ya está en uso."
        return
    }

    $dbPassword = New-RandomHex -ByteCount 32
    $rootPassword = New-RandomHex -ByteCount 32
    $redisPassword = ""
    $redisImage = ""
    $Ctx.Secrets = @($dbPassword, $rootPassword)
    if ($withRedis) {
        $redisPassword = New-RandomHex -ByteCount 32
        $redisImage = $Ctx.RedisImageReference
        $Ctx.Secrets = @($dbPassword, $rootPassword, $redisPassword)
    }
    $Ctx.DockerEnvironment = New-DockerChildEnvironment -Names $Ctx.Names -DbPassword $dbPassword -RootPassword $rootPassword -RedisPassword $redisPassword
    $Ctx.PythonEnvironment = New-PythonChildEnvironment -Names $Ctx.Names -SettingsModule $Ctx.SettingsModule -DbPassword $dbPassword -ReportDirectory $Ctx.OutputDirectory -RedisPassword $redisPassword

    $override = Join-Path $Ctx.ComposeDirectory "invocation.override.yml"
    $emptyEnv = Join-Path $Ctx.ComposeDirectory "empty.env"
    Write-Utf8File -Path $override -Content (New-ComposeOverride -InvocationId $Ctx.InvocationId -OwnerProof $Ctx.OwnerProof -ImageReference $Ctx.ImageReference -RedisImageReference $redisImage)
    Write-Utf8File -Path $emptyEnv -Content ""

    # AUD-016: directorio de proyecto limpio, imagen fijada disponible y
    # configuración renderizada válida ANTES de crear ningún recurso.
    $composeProblem = Test-ComposeDirectoryClean -Ctx $Ctx
    if ($composeProblem) { Set-StageFailure $Ctx PREFLIGHT_REJECTED ("Compose: " + $composeProblem); return }
    $imageProblem = Test-PinnedImageAvailable -Ctx $Ctx
    if ($imageProblem) { Set-StageFailure $Ctx PREFLIGHT_REJECTED ("Imagen: " + $imageProblem); return }
    if ($withRedis) {
        $redisImageProblem = Test-PinnedRedisImageAvailable -Ctx $Ctx
        if ($redisImageProblem) { Set-StageFailure $Ctx PREFLIGHT_REJECTED ("Imagen Redis: " + $redisImageProblem); return }
    }
    $base = Get-ComposeBaseArguments -Ctx $Ctx
    $rendered = Invoke-DockerCommand -Ctx $Ctx -TimeoutSeconds $timeouts.ComposeTimeoutSeconds -Arguments ($base + @("config", "--format", "json")) -NoOutputLog
    if ($rendered.StartError -or $rendered.TimedOut -or $rendered.ExitCode -ne 0 -or -not $rendered.TerminationConfirmed) {
        Set-StageFailure $Ctx PREFLIGHT_REJECTED "Compose: no se pudo renderizar la configuración."
        return
    }
    $configProblem = Test-ComposeRenderedConfig -Json ([string]$rendered.Stdout) -Ctx $Ctx
    if ($configProblem) { Set-StageFailure $Ctx PREFLIGHT_REJECTED ("Compose: " + $configProblem); return }

    $up = $null
    try {
        $up = Invoke-DockerCommand -Ctx $Ctx -TimeoutSeconds $timeouts.ComposeTimeoutSeconds -Arguments ($base + @("up", "-d", "--no-build", "--no-recreate", "--pull", "never"))
    }
    finally {
        Update-Ownership -Ctx $Ctx
    }
    if ($Ctx.OwnershipProblems.Count -gt 0) {
        Set-StageFailure $Ctx PREEXISTING_OR_OWNERSHIP ("Propiedad no demostrada: " + ($Ctx.OwnershipProblems -join "; "))
        return
    }
    if ($up.TimedOut -or $up.ExitCode -ne 0 -or -not $up.TerminationConfirmed -or -not $Ctx.Owned.ContainerId -or ($withRedis -and -not (Get-CtxRedisContainerId -Ctx $Ctx))) {
        Set-StageFailure $Ctx INFRASTRUCTURE_FAILED "docker compose up no completó la creación."
        return
    }
    Set-RunState -Ctx $Ctx -State "ENVIRONMENT_CREATED"

    if (-not (Wait-ContainerHealthy -Ctx $Ctx)) { Set-StageFailure $Ctx INFRASTRUCTURE_FAILED "El contenedor no alcanzó healthy."; return }
    $identityProblem = Test-ContainerIdentity -Ctx $Ctx
    if ($identityProblem) { Set-StageFailure $Ctx PREFLIGHT_REJECTED $identityProblem; return }
    if ($withRedis) {
        if (-not (Wait-ContainerHealthy -Ctx $Ctx -ContainerId (Get-CtxRedisContainerId -Ctx $Ctx))) { Set-StageFailure $Ctx INFRASTRUCTURE_FAILED "El contenedor Redis no alcanzó healthy."; return }
        $redisProblem = Test-RedisIdentity -Ctx $Ctx
        if ($redisProblem) { Set-StageFailure $Ctx PREFLIGHT_REJECTED $redisProblem; return }
    }

    switch ($Ctx.Mode) {
        "EnvironmentOnly" {
            $r = Invoke-PythonChild -Ctx $Ctx -Step "preflight" -Arguments (Get-PreflightArguments -Ctx $Ctx -Command "preflight" -ExpectSchema "empty") -TimeoutSeconds $timeouts.PreflightTimeoutSeconds
            $code = ConvertTo-StageCode -Result $r
            if ($code -ne 0) { Set-StageFailure $Ctx (Get-ExitCodeName $code) "Preflight no superado."; return }
            Set-RunState -Ctx $Ctx -State "PREFLIGHT_PASSED"
        }
        "SqlMigrateOnly" {
            $r = Invoke-PythonChild -Ctx $Ctx -Step "sqlmigrate" -Arguments (Get-PreflightArguments -Ctx $Ctx -Command "sqlmigrate") -TimeoutSeconds $timeouts.MigrateTimeoutSeconds
            $code = ConvertTo-StageCode -Result $r
            if ($code -ne 0) { Set-StageFailure $Ctx (Get-ExitCodeName $code) "sqlmigrate no completado."; return }
            Set-RunState -Ctx $Ctx -State "PREFLIGHT_PASSED" -Detail "sqlmigrate ejecutado tras preflight"
        }
        "Validate" {
            # El subcomando 'migrate' ejecuta el preflight en el mismo proceso y
            # solo migra si es OK. Ruta adicional (AUD-002): pytest-django con
            # --reuse-db vuelve a llamar a migrate (keepdb) dentro de pytest; esa
            # ruta no ejecuta el preflight completo y la protegen la guarda de
            # conftest.py y el preflight 'migrated' previo.
            $r = Invoke-PythonChild -Ctx $Ctx -Step "migrate" -Arguments (Get-PreflightArguments -Ctx $Ctx -Command "migrate") -TimeoutSeconds $timeouts.MigrateTimeoutSeconds
            $code = ConvertTo-StageCode -Result $r
            if ($code -ne 0) { Set-StageFailure $Ctx (Get-ExitCodeName $code) "Preflight o migración no superados."; return }
            Set-RunState -Ctx $Ctx -State "PREFLIGHT_PASSED"
            Set-RunState -Ctx $Ctx -State "MIGRATIONS_COMPLETED"
            $check = Invoke-PythonChild -Ctx $Ctx -Step "preflight-migrated" -Arguments (Get-PreflightArguments -Ctx $Ctx -Command "preflight" -ExpectSchema "migrated") -TimeoutSeconds $timeouts.PreflightTimeoutSeconds
            $code = ConvertTo-StageCode -Result $check
            if ($code -ne 0) { Set-StageFailure $Ctx (Get-ExitCodeName $code) "Preflight posterior a la migración no superado."; return }
            $tests = Invoke-PythonChild -Ctx $Ctx -Step "pytest" -Arguments (Get-PytestArguments -Ctx $Ctx) -TimeoutSeconds $timeouts.PytestTimeoutSeconds
            $code = ConvertTo-StageCode -Result $tests -Kind pytest
            if ($code -ne 0) { Set-StageFailure $Ctx (Get-ExitCodeName $code) "pytest terminó con código $($tests.ExitCode)."; return }
            Set-RunState -Ctx $Ctx -State "TESTS_COMPLETED"
        }
    }
}

function Get-ExitCodeName {
    param([int]$Code)
    foreach ($key in $script:DisposableExitCodes.Keys) {
        if ($script:DisposableExitCodes[$key] -eq $Code) { return $key }
    }
    return "INFRASTRUCTURE_FAILED"
}

function Get-JsonValue {
    <# Lee una propiedad anidada sin fallar con StrictMode si no existe. #>
    param($Object, [string[]]$Path)
    $current = $Object
    foreach ($segment in $Path) {
        if ($null -eq $current) { return $null }
        $property = $current.PSObject.Properties[$segment]
        if ($null -eq $property) { return $null }
        $current = $property.Value
    }
    return $current
}

function Invoke-CleanupOnlyMode {
    param($Normalized, [string]$Root)
    try {
        $state = Get-Content -LiteralPath $Normalized.StateFile -Raw -Encoding UTF8 | ConvertFrom-Json
    }
    catch {
        Write-Host "No se pudo leer -StateFile."
        return (Get-DisposableExitCode PARAMETERS_INVALID)
    }
    $stateRunId = Get-JsonValue -Object $state -Path @("RunId")
    $stateInvocation = Get-JsonValue -Object $state -Path @("InvocationId")
    if ([string]$stateRunId -cne $Normalized.RunId -or -not $stateInvocation -or [string]$stateInvocation -notmatch '^[0-9a-f]{32}$') {
        Write-Host "-StateFile no corresponde al RunId indicado o no registra InvocationId."
        return (Get-DisposableExitCode PREEXISTING_OR_OWNERSHIP)
    }
    # AUD-014: el estado debe ser auténtico (DPAPI del mismo usuario + HMAC). Aun
    # así, no autoriza por sí solo: cada recurso se revalida contra Docker con
    # la prueba de propiedad derivada del token.
    $integrity = Test-StateIntegrity -State $state -Names (Get-DisposableNames -Id $Normalized.RunId) -InvocationId ([string]$stateInvocation)
    if (-not $integrity.Ok) {
        Write-Host ("-StateFile no auténtico: " + $integrity.Reason + ". No se elimina nada.")
        return (Get-DisposableExitCode PREEXISTING_OR_OWNERSHIP)
    }
    $outputDirectory = Split-Path -Parent $Normalized.StateFile
    try { Assert-DirectoryInUse -Path $outputDirectory -Root $Root }
    catch {
        Write-Host ("Ruta no permitida en el momento de uso: " + $_.Exception.Message)
        return (Get-DisposableExitCode PARAMETERS_INVALID)
    }
    $ctx = New-OrchestratorContext -Normalized $Normalized -Root $Root -RunId $Normalized.RunId -InvocationId ([string]$stateInvocation) -OutputDirectory $outputDirectory
    $ctx.StateFile = $null
    # F10.3.4-A: el contenedor Redis solo se recupera si el estado v2 auténtico lo declara.
    $ctx.WithRedis = [bool]$integrity.WithRedis
    $ctx.OwnerToken = $integrity.Token
    $ctx.OwnerProof = Get-OwnerProof -Token $integrity.Token
    $recordedVolumes = @(Get-JsonValue -Object $state -Path @("Owned", "Volumes") | ForEach-Object { [string]$_ } | Where-Object { $_ })
    $ctx.LogDirectory = Join-Path $outputDirectory ("cleanup-" + (New-RandomHex -ByteCount 4))
    New-Item -ItemType Directory -Path $ctx.LogDirectory -Force | Out-Null
    try { Assert-DirectoryInUse -Path $ctx.LogDirectory -Root $Root }
    catch {
        Write-Host ("Ruta no permitida en el momento de uso: " + $_.Exception.Message)
        return (Get-DisposableExitCode PARAMETERS_INVALID)
    }
    $ctx.ComposeDirectory = $outputDirectory
    $ctx.DockerExe = Get-DockerExecutable
    if (-not $ctx.DockerExe) {
        Write-Host "Docker CLI no encontrado."
        return (Get-DisposableExitCode INFRASTRUCTURE_FAILED)
    }
    if (-not (Enter-OrchestratorLock)) {
        Write-Host "Otra invocación del orquestador está en curso."
        return (Get-DisposableExitCode PREEXISTING_OR_OWNERSHIP)
    }
    $ctx.Phase = "cleanup"
    $code = 0
    $recoveryState = "RECOVERY_COMPLETED"
    $results = @{ Container = "NotChecked"; Network = "NotChecked" }
    try {
        try { $ctx.DockerContext = Resolve-DockerContext -Ctx $ctx }
        catch {
            Write-Host $_.Exception.Message
            $code = Get-DisposableExitCode INFRASTRUCTURE_FAILED
            $recoveryState = "RECOVERY_FAILED"
        }
        if ($code -eq 0) {
            $expected = Get-ExpectedOwnership -Names $ctx.Names -InvocationId $ctx.InvocationId -OwnerProof $ctx.OwnerProof
            $deadline = Get-CleanupDeadline -Ctx $ctx
            $refused = $false
            $failed = $false
            $incomplete = $false
            $kinds = @("container", "network")
            if ($ctx.WithRedis) {
                $kinds = @("redis", "container", "network")
                $results["redis"] = "NotChecked"
            }
            foreach ($kind in $kinds) {
                $name = $ctx.Names.NetworkName
                $recorded = Get-JsonValue -Object $state -Path @("Owned", "NetworkId")
                $dockerKind = $kind
                $kindExpected = $expected
                if ($kind -eq "container") { $name = $ctx.Names.ContainerName; $recorded = Get-JsonValue -Object $state -Path @("Owned", "ContainerId") }
                if ($kind -eq "redis") {
                    $name = $ctx.Names.RedisContainerName
                    $recorded = Get-JsonValue -Object $state -Path @("Owned", "RedisContainerId")
                    $dockerKind = "container"
                    $kindExpected = Get-ExpectedRedisOwnership -Names $ctx.Names -InvocationId $ctx.InvocationId -OwnerProof $ctx.OwnerProof
                }
                $ref = $name
                if ($recorded) { $ref = [string]$recorded }
                $timeout = Get-CleanupStepTimeout -Ctx $ctx -Deadline $deadline
                if ($timeout -le 0) { $results[$kind] = "Incomplete"; $incomplete = $true; continue }
                $resource = Get-DockerResource -Ctx $ctx -Kind $dockerKind -Ref $ref -TimeoutSeconds $timeout
                $ownership = Get-ResourceOwnership -Resource $resource -Expected $kindExpected
                if ($ownership -eq "Absent") { $results[$kind] = "AlreadyAbsent"; Write-Host "$kind ${name}: ausente."; continue }
                if ($ownership -ne "Owned") {
                    $results[$kind] = "Refused:$ownership"
                    Write-Host "$kind ${name}: propiedad no demostrada ($ownership); no se elimina."
                    $refused = $true
                    continue
                }
                $outcome = Remove-OwnedResource -Ctx $ctx -Kind $dockerKind -Id $resource.Id -Expected $kindExpected -Deadline $deadline
                $results[$kind] = $outcome
                Write-Host "$kind ${name}: $outcome"
                if ($outcome -like "Refused:*") { $refused = $true }
                elseif ($outcome -eq "Incomplete") { $incomplete = $true }
                elseif ($outcome -notin @("Removed", "AlreadyAbsent")) { $failed = $true }
            }
            # R-ORQ-8: volúmenes anónimos registrados, solo si el contenedor ya no existe.
            if ($recordedVolumes.Count -gt 0) {
                $volumeOutcome = @{ Ok = $true; Incomplete = $false; Failed = $false; Volumes = @() }
                $containersGone = ($results["container"] -in @("Removed", "AlreadyAbsent"))
                if ($ctx.WithRedis) { $containersGone = $containersGone -and ($results["redis"] -in @("Removed", "AlreadyAbsent")) }
                Invoke-OwnedVolumeCleanup -Ctx $ctx -Outcome $volumeOutcome -Expected $expected -Deadline $deadline -Volumes $recordedVolumes -ContainerGone $containersGone
                $results["volumes"] = ($volumeOutcome.Volumes -join "; ")
                if ($volumeOutcome.Failed) { $failed = $true }
                elseif ($volumeOutcome.Incomplete) { $incomplete = $true }
            }
            if ($refused) { $code = Get-DisposableExitCode PREEXISTING_OR_OWNERSHIP; $recoveryState = "RECOVERY_REFUSED" }
            elseif ($failed) { $code = Get-DisposableExitCode CLEANUP_FAILED; $recoveryState = "RECOVERY_FAILED" }
            elseif ($incomplete) { $code = Get-DisposableExitCode CLEANUP_FAILED; $recoveryState = "RECOVERY_INCOMPLETE" }
        }
        if ($ctx.TerminationFailures.Count -gt 0 -and $code -eq 0) {
            $code = Get-DisposableExitCode CLEANUP_FAILED
            $recoveryState = "RECOVERY_FAILED"
        }
    }
    finally {
        Exit-OrchestratorLock
    }
    try {
        # El archivo de estado se revalida justo antes de reescribirlo (AUD-013).
        [void](Resolve-StateFilePath -Root $Root -Path $Normalized.StateFile)
        Update-RecoveryState -StateFile $Normalized.StateFile -State $state -RecoveryState $recoveryState -Results $results -ExitCode $code -TerminationFailures $ctx.TerminationFailures.ToArray()
    }
    catch {
        Write-Host ("No se pudo actualizar -StateFile: " + $_.Exception.Message)
        if ($code -eq 0) { $code = Get-DisposableExitCode INFRASTRUCTURE_FAILED }
    }
    return $code
}

function Update-RecoveryState {
    <#
    Registra el resultado de -CleanupOnly en el run-state.json existente sin
    alterar sus campos previos (AUD-011): State pasa a RECOVERY_*, se añade
    una entrada al historial y un objeto Recovery.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$StateFile,
        [Parameter(Mandatory = $true)]$State,
        [Parameter(Mandatory = $true)][string]$RecoveryState,
        [Parameter(Mandatory = $true)][hashtable]$Results,
        [int]$ExitCode,
        [object[]]$TerminationFailures = @()
    )
    $at = (Get-Date).ToUniversalTime().ToString("o")
    $history = @()
    $existing = Get-JsonValue -Object $State -Path @("History")
    if ($null -ne $existing) { $history = @($existing) }
    $history += @{ State = $RecoveryState; At = $at; Detail = ("CleanupOnly exit=" + $ExitCode) }
    $recovery = @{
        At = $at; State = $RecoveryState; ExitCode = $ExitCode
        Container = $Results["container"]; Network = $Results["network"]; Volumes = $Results["volumes"]
        TerminationFailures = @($TerminationFailures)
    }
    if ($Results.ContainsKey("redis")) { $recovery["Redis"] = $Results["redis"] }
    $State | Add-Member -NotePropertyName "State" -NotePropertyValue $RecoveryState -Force
    $State | Add-Member -NotePropertyName "History" -NotePropertyValue $history -Force
    $State | Add-Member -NotePropertyName "Recovery" -NotePropertyValue $recovery -Force
    $tmp = $StateFile + ".tmp"
    Write-Utf8File -Path $tmp -Content ($State | ConvertTo-Json -Depth 8)
    Move-Item -LiteralPath $tmp -Destination $StateFile -Force
}

function Invoke-DisposableOrchestrator {
    <#
    Flujo completo. Devuelve el código de salida de la tabla central.
    Options: Root, CleanupOnly, RunId, Validate, SqlMigrateOnly, Tests,
    TestsBound, SettingsModule, SettingsModuleBound, OutputDirectory, StateFile
    y los timeouts de $script:TimeoutRanges.
    #>
    param([Parameter(Mandatory = $true)][hashtable]$Options)
    $Options = Complete-OrchestratorOptions -Options $Options
    if ([string]::IsNullOrWhiteSpace([string]$Options.Root)) {
        Write-Host "Parámetros no válidos: falta la raíz del repositorio."
        return (Get-DisposableExitCode PARAMETERS_INVALID)
    }
    $check = Test-OrchestratorOptions -Options $Options
    if (-not $check.Ok) {
        Write-Host ("Parámetros no válidos: " + $check.Message)
        return (Get-DisposableExitCode PARAMETERS_INVALID)
    }
    $normalized = $check.Normalized
    $root = Get-NormalizedFullPath -Path $Options.Root
    if ($normalized.Mode -eq "CleanupOnly") {
        $code = Invoke-CleanupOnlyMode -Normalized $normalized -Root $root
        Write-Host (Get-FinalMessage -Code $code -Mode "CleanupOnly")
        return $code
    }

    if (-not (Enter-OrchestratorLock)) {
        Write-Host "Otra invocación del orquestador está en curso."
        return (Get-DisposableExitCode PREEXISTING_OR_OWNERSHIP)
    }
    $ctx = $null
    $completed = $false
    $interrupted = $false
    try {
        $runId = $normalized.RunId
        if (-not $runId) { $runId = New-RandomHex -ByteCount 6 }
        $invocationId = [guid]::NewGuid().ToString("N")
        $outputDirectory = $normalized.OutputDirectory
        if (-not $outputDirectory) {
            $outputDirectory = Resolve-SafeOutputDirectory -Root $root -Path (Join-Path $env:LOCALAPPDATA ("agrotech-disposable\runs\{0}-{1}" -f $runId, $invocationId.Substring(0, 8)))
        }
        New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
        $pathProblem = $null
        try { Assert-DirectoryInUse -Path $outputDirectory -Root $root } catch { $pathProblem = $_.Exception.Message }
        if ($pathProblem) {
            Write-Host ("Ruta no permitida en el momento de uso: " + $pathProblem)
            $completed = $true
            return (Get-DisposableExitCode PARAMETERS_INVALID)
        }
        $ctx = New-OrchestratorContext -Normalized $normalized -Root $root -RunId $runId -InvocationId $invocationId -OutputDirectory $outputDirectory
        New-Item -ItemType Directory -Path $ctx.LogDirectory -Force | Out-Null
        New-Item -ItemType Directory -Path $ctx.ComposeDirectory -Force | Out-Null
        try {
            Assert-DirectoryInUse -Path $ctx.LogDirectory -Root $root
            Assert-DirectoryInUse -Path $ctx.ComposeDirectory -Root $root
        }
        catch {
            Write-Host ("Ruta no permitida en el momento de uso: " + $_.Exception.Message)
            $ctx = $null
            $completed = $true
            return (Get-DisposableExitCode PARAMETERS_INVALID)
        }
        $secret = $null
        try { $secret = New-OwnerSecret -InvocationId $invocationId } catch { $secret = $null }
        if ($null -ne $secret) {
            $ctx.OwnerToken = $secret.Token
            $ctx.OwnerProof = $secret.Proof
            $ctx.ProtectedToken = $secret.Protected
        }
        Write-Host "RunId: $runId"
        Write-Host "InvocationId: $invocationId"
        Write-Host "Contenedor: $($ctx.Names.ContainerName) | Red: $($ctx.Names.NetworkName) | Proyecto: $($ctx.Names.ProjectName)"
        if ($ctx.WithRedis) { Write-Host "Redis: $($ctx.Names.RedisContainerName) en 127.0.0.1:$($script:RedisPort)" }
        Write-Host "Resultados: $outputDirectory"
        Write-Host "Recuperación: -CleanupOnly -RunId $runId -StateFile `"$($ctx.StateFile)`""
        Set-RunState -Ctx $ctx -State "NOT_STARTED"
        $dockerExe = Get-DockerExecutable
        if ($null -eq $ctx.OwnerToken) {
            Set-StageFailure $ctx INFRASTRUCTURE_FAILED "No se pudo generar el token de propiedad protegido con DPAPI."
        }
        elseif (-not $dockerExe) {
            Set-StageFailure $ctx INFRASTRUCTURE_FAILED "Docker CLI no encontrado."
        }
        elseif (-not (Test-Path -LiteralPath $ctx.Python)) {
            Set-StageFailure $ctx INFRASTRUCTURE_FAILED "No se encontró el intérprete de .venv."
        }
        elseif (-not (Test-Path -LiteralPath $ctx.ComposeFile -PathType Leaf) -or (Get-ReparsePointInChain -Path $ctx.ComposeFile -From $root) -or (Test-PathPhysicallyWithin -Path $ctx.ComposeFile -Parent $root) -ne $true) {
            Set-StageFailure $ctx INFRASTRUCTURE_FAILED "docker-compose.disposable.yml no existe o su ruta física está fuera del repositorio."
        }
        elseif ($ctx.WithRedis -and (-not (Test-Path -LiteralPath $ctx.RedisComposeFile -PathType Leaf) -or (Get-ReparsePointInChain -Path $ctx.RedisComposeFile -From $root) -or (Test-PathPhysicallyWithin -Path $ctx.RedisComposeFile -Parent $root) -ne $true)) {
            Set-StageFailure $ctx INFRASTRUCTURE_FAILED "$($script:RedisComposeFileName) no existe o su ruta física está fuera del repositorio."
        }
        else {
            $ctx.DockerExe = $dockerExe
            Invoke-EnvironmentStages -Ctx $ctx
        }
        $completed = $true
    }
    catch [System.Management.Automation.PipelineStoppedException], [System.OperationCanceledException] {
        $interrupted = $true
    }
    catch {
        $internal = "Error interno: " + $_.Exception.Message
        if ($null -ne $ctx) {
            try { Set-StageFailure $ctx INFRASTRUCTURE_FAILED $internal }
            catch {
                Write-Host ("No se pudo persistir el fallo en run-state.json: " + $_.Exception.Message)
                if ($ctx.StageCode -eq 0) { $ctx.StageCode = Get-DisposableExitCode INFRASTRUCTURE_FAILED; $ctx.StageMessage = $internal }
            }
        }
        else { Write-Host $internal }
        $completed = $true
    }
    finally {
        if (-not $completed) { $interrupted = $true }
        if ($null -ne $ctx) {
            Complete-OrchestratorRun -Ctx $ctx -Interrupted $interrupted
        }
        Exit-OrchestratorLock
    }
    if ($null -eq $ctx) {
        if ($interrupted) { return (Get-DisposableExitCode INTERRUPTED) }
        return (Get-DisposableExitCode INFRASTRUCTURE_FAILED)
    }
    if ($ctx.StageMessage) { Write-Host ("Detalle: " + $ctx.StageMessage) }
    if ($ctx.Cleanup -and -not $ctx.Cleanup.Ok) {
        $redisText = ""
        if ($ctx.Cleanup.ContainsKey("Redis")) { $redisText = " redis=" + $ctx.Cleanup.Redis }
        Write-Host ("Limpieza: contenedor=" + $ctx.Cleanup.Container + $redisText + " red=" + $ctx.Cleanup.Network + " problemas=" + (@($ctx.Cleanup.Problems) -join "; "))
    }
    foreach ($failure in $ctx.TerminationFailures) {
        Write-Host ("Terminación no confirmada en '" + $failure.Step + "' (" + $failure.Phase + "): PID " + (@($failure.Survivors) -join ","))
    }
    Write-Host (Get-FinalMessage -Code $ctx.ExitCode -Mode $ctx.Mode)
    return $ctx.ExitCode
}

function Complete-OrchestratorRun {
    <#
    Limpieza, código final y estado persistido (AUD-007, AUD-008, AUD-011).
    - Una terminación no confirmada en una etapa impide el éxito (40).
    - Una terminación no confirmada en la limpieza la marca como fallida.
    - Si no puede persistirse el estado final, no se devuelve 0 (40).
    #>
    param([Parameter(Mandatory = $true)]$Ctx, [bool]$Interrupted)
    $stageFailures = @($Ctx.TerminationFailures | Where-Object { $_.Phase -ne "cleanup" })
    if ($stageFailures.Count -gt 0) {
        if ($Ctx.StageCode -eq 0) {
            $Ctx.StageCode = Get-DisposableExitCode INFRASTRUCTURE_FAILED
            $Ctx.StageMessage = "Proceso hijo con terminación no confirmada."
        }
    }
    $Ctx.Phase = "cleanup"
    $cleanupRequired = [bool]($Ctx.Owned.ContainerId -or $Ctx.Owned.NetworkId -or (Get-CtxRedisContainerId -Ctx $Ctx) -or $Ctx.OwnershipProblems.Count -gt 0)
    $before = $Ctx.TerminationFailures.Count
    $cleanup = @{ Ok = $true; Incomplete = $false; Failed = $false; Container = "NotOwned"; Network = "NotOwned"; Problems = @() }
    if ($cleanupRequired) {
        try { $cleanup = Invoke-OwnedCleanup -Ctx $Ctx }
        catch { $cleanup = @{ Ok = $false; Incomplete = $false; Failed = $true; Container = "Error"; Network = "Error"; Problems = @($_.Exception.Message) } }
    }
    if ($Ctx.TerminationFailures.Count -gt $before) {
        $cleanup.Ok = $false
        $cleanup.Failed = $true
    }
    $Ctx.Cleanup = $cleanup
    $Ctx.ExitCode = Get-FinalExitCode -StageCode $Ctx.StageCode -CleanupRequired $cleanupRequired -CleanupOk ([bool]$cleanup.Ok) -Interrupted $Interrupted
    $finalState = Resolve-CleanupFinalState -Required $cleanupRequired -Outcome $cleanup
    if ($Interrupted) { $finalState = "INTERRUPTED" }
    try {
        Set-RunState -Ctx $Ctx -State $finalState -Detail ("exit=" + $Ctx.ExitCode)
    }
    catch {
        # No se silencia (AUD-011): sin estado persistido no se declara éxito.
        Write-Host ("No se pudo persistir el estado final en run-state.json: " + $_.Exception.Message)
        if ($Ctx.ExitCode -eq 0) { $Ctx.ExitCode = Get-DisposableExitCode INFRASTRUCTURE_FAILED }
    }
}
