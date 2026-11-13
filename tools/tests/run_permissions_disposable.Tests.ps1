<#
Pruebas aisladas del orquestador desechable (Pester 3.4.0, Windows PowerShell 5.1).

No ejecutan Docker ni se conectan a bases de datos: Invoke-DockerCommand,
Invoke-PythonChild, Test-LoopbackPortFree, el mutex y Get-DockerExecutable se
sustituyen por mocks con un estado Docker simulado. Las únicas ejecuciones
reales son procesos locales inocuos (python -I -B -c y powershell Start-Sleep)
para validar timeouts, terminación del árbol y entorno hijo. Todos los archivos
se crean en TestDrive (fuera del repositorio).

Ejecución: powershell -NoProfile -Command "Invoke-Pester -Script tools\tests\run_permissions_disposable.Tests.ps1"
#>

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $here "..\disposable_orchestrator_lib.ps1")
$RepoRoot = (Resolve-Path (Join-Path $here "..\..")).Path
$RepoPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$PowerShellExe = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"

function New-FakeResult {
    param([int]$ExitCode = 0, [string]$Stdout = "", [string]$Stderr = "", [switch]$TimedOut, [switch]$Unconfirmed, [int[]]$Survivors = @())
    $code = $ExitCode
    if ($TimedOut) { $code = $null }
    return [pscustomobject]@{
        ExitCode = $code; TimedOut = [bool]$TimedOut; TerminationConfirmed = (-not $Unconfirmed)
        Survivors = @($Survivors); LingeringDescendants = @(); Stdout = $Stdout; Stderr = $Stderr; DurationMs = 1; StartError = $null
    }
}

# Secretos ficticios que nunca deben aparecer en logs (AUD-006).
$FakeDbSecret = "FicticiaDbSecreta-7f3a91"
$FakeRootSecret = "FicticiaRootSecreta-c2e8d4"

function Get-LogTextUnder {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return "" }
    return ((Get-ChildItem -LiteralPath $Path -Recurse -File | ForEach-Object { Get-Content -LiteralPath $_.FullName -Raw -Encoding UTF8 }) -join "`n")
}

function New-FakeRoot {
    param([string]$Base)
    $root = Join-Path $Base ("repo-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
    foreach ($dir in @("apps\farms\tests", "apps\permissions\tests", "apps\farms\other", ".venv\Scripts", "tests")) {
        New-Item -ItemType Directory -Path (Join-Path $root $dir) -Force | Out-Null
    }
    Set-Content -LiteralPath (Join-Path $root "apps\farms\tests\test_a.py") -Value "" -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $root "apps\farms\tests\helper.py") -Value "" -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $root "apps\farms\other\test_b.py") -Value "" -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $root "docker-compose.disposable.yml") -Value "services: {}" -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $root ".venv\Scripts\python.exe") -Value "" -Encoding ASCII
    return $root
}

$TestDigest = "sha256:" + ("a" * 64)
$TestRedisDigest = "sha256:" + ("b" * 64)
$script:TestSecrets = @{}

function Get-TestSecret {
    <# Secreto de propiedad real (DPAPI, usuario actual) cacheado por InvocationId. #>
    param([Parameter(Mandatory = $true)][string]$InvocationId)
    if (-not $script:TestSecrets.ContainsKey($InvocationId)) {
        $script:TestSecrets[$InvocationId] = New-OwnerSecret -InvocationId $InvocationId
    }
    return $script:TestSecrets[$InvocationId]
}

function New-FakeDockerState {
    return @{
        Containers = @{}; Networks = @{}; Volumes = @{}; Calls = New-Object System.Collections.Generic.List[string]
        ComposeMode = "ok"; RmFails = $false; VolumeRmFails = $false; Endpoint = "npipe:////./pipe/dockerDesktopLinuxEngine"
        Port = "127.0.0.1:3317"; Health = "healthy"
        Grants = @(
            "GRANT USAGE ON *.* TO ``agrotech_perm_validation``@``%`` IDENTIFIED BY PASSWORD '*HASH'",
            "GRANT ALL PRIVILEGES ON ``__DB__``.* TO ``agrotech_perm_validation``@``%``",
            "ROLE:NONE", "ROLES:0")
        PythonCalls = New-Object System.Collections.Generic.List[string]
        PythonArgs = New-Object System.Collections.Generic.List[string]
        PythonCodes = @{}; PythonUnconfirmed = @{}; PythonHook = $null; PortFree = $true
        ExecArgs = New-Object System.Collections.Generic.List[string]
        RmUnconfirmed = $false
        ImageDigests = @($TestDigest); ConfigMutator = $null; ConfigFails = $false
        NewContainerMounts = @(); LsFailure = $null; VolumeLsFailure = $null
        # F10.3.4-A: Redis desechable simulado.
        RedisImageDigests = @($TestRedisDigest); RedisPort = "127.0.0.1:16379"; RedisUpMode = "ok"
        RedisPing = "(error) NOAUTH Authentication required."; RedisSet = "OK"; RedisMounts = @()
    }
}

function Add-FakeContainer {
    param([string]$Name, [hashtable]$Labels, [string]$Hostname, [object[]]$Mounts = @())
    $id = (New-RandomHex -ByteCount 32)
    $script:Fake.Containers[$id] = @{ Id = $id; Name = $Name; Labels = $Labels; Hostname = $Hostname; Mounts = @($Mounts) }
    return $id
}

function Add-FakeNetwork {
    param([string]$Name, [hashtable]$Labels)
    $id = (New-RandomHex -ByteCount 32)
    $script:Fake.Networks[$id] = @{ Id = $id; Name = $Name; Labels = $Labels }
    return $id
}

function Add-FakeVolume {
    param([string]$Name, [hashtable]$Labels = @{})
    $script:Fake.Volumes[$Name] = @{ Name = $Name; Labels = $Labels }
}

function New-AnonymousVolumeName { return (New-RandomHex -ByteCount 32) }

function Find-FakeResource {
    param([hashtable]$Table, [string]$Ref)
    foreach ($item in $Table.Values) {
        if ($item.Id -eq $Ref -or $item.Name -eq $Ref) { return $item }
    }
    return $null
}

function Get-BaseLabels {
    <#
    Etiquetas base; con InvocationId añade la prueba de propiedad del secreto
    de prueba de esa invocación, salvo que se indique -OwnerProof.
    #>
    param([string]$RunId, [string]$InvocationId, [string]$OwnerProof)
    $labels = @{ "agrotech.disposable" = "true"; "agrotech.run-id" = $RunId; "agrotech.purpose" = "permissions-validation" }
    if ($InvocationId) {
        $labels["agrotech.invocation-id"] = $InvocationId
        if (-not $PSBoundParameters.ContainsKey("OwnerProof")) { $OwnerProof = (Get-TestSecret -InvocationId $InvocationId).Proof }
        if ($OwnerProof) { $labels["agrotech.owner-proof"] = $OwnerProof }
    }
    return $labels
}

function Get-FakeOverride {
    param([string[]]$Arguments)
    $a = @($Arguments)
    $fIndexes = @(0..($a.Count - 1) | Where-Object { $a[$_] -eq "-f" })
    # El override de la invocación es siempre el último -f.
    $text = Get-Content -LiteralPath $a[$fIndexes[$fIndexes.Count - 1] + 1] -Raw
    $images = [regex]::Matches($text, 'image: "([^"]+)"')
    $redisImage = ""
    if ($images.Count -gt 1) { $redisImage = $images[1].Groups[1].Value }
    return [pscustomobject]@{
        Invocation = ([regex]::Match($text, 'agrotech\.invocation-id: "([0-9a-f]+)"')).Groups[1].Value
        Proof = ([regex]::Match($text, 'agrotech\.owner-proof: "([0-9a-f]+)"')).Groups[1].Value
        Image = $images[0].Groups[1].Value
        RedisImage = $redisImage
        FileCount = $fIndexes.Count
    }
}

function New-FakeRenderedConfig {
    param($Ctx, $Override)
    $labels = Get-BaseLabels -RunId $Ctx.Names.RunId -InvocationId $Override.Invocation -OwnerProof $Override.Proof
    $port = [ordered]@{ mode = "ingress"; host_ip = "127.0.0.1"; target = 3306; published = "3317"; protocol = "tcp" }
    $service = [ordered]@{
        container_name = $Ctx.Names.ContainerName; hostname = $Ctx.Names.ContainerHostname; image = $Override.Image
        labels = $labels; ports = @($port); networks = [ordered]@{ disposable = $null }
        tmpfs = @("/var/lib/mysql:rw,noexec,nosuid,size=2g")
        environment = [ordered]@{ MARIADB_PASSWORD = $FakeDbSecret; MARIADB_ROOT_PASSWORD = $FakeRootSecret }
    }
    $network = [ordered]@{ name = $Ctx.Names.NetworkName; labels = (Get-BaseLabels -RunId $Ctx.Names.RunId -InvocationId $Override.Invocation -OwnerProof $Override.Proof) }
    $config = [ordered]@{ name = $Ctx.Names.ProjectName; services = [ordered]@{ mariadb = $service }; networks = [ordered]@{ disposable = $network } }
    if ($Override.PSObject.Properties["RedisImage"] -and $Override.RedisImage) {
        $config.services["redis"] = [ordered]@{
            container_name = $Ctx.Names.RedisContainerName; hostname = $Ctx.Names.RedisContainerHostname; image = $Override.RedisImage
            labels = (Get-BaseLabels -RunId $Ctx.Names.RunId -InvocationId $Override.Invocation -OwnerProof $Override.Proof)
            ports = @([ordered]@{ mode = "ingress"; host_ip = "127.0.0.1"; target = 6379; published = "16379"; protocol = "tcp" })
            networks = [ordered]@{ disposable = $null }; tmpfs = @("/data:rw,noexec,nosuid,size=64m")
            command = @("redis-server", "--requirepass", "FicticiaRedisSecreta-91ab"); environment = [ordered]@{ REDISCLI_AUTH = "FicticiaRedisSecreta-91ab" }
        }
    }
    if ($null -ne $script:Fake.ConfigMutator) { & $script:Fake.ConfigMutator $config }
    return (ConvertTo-Json -InputObject $config -Depth 10)
}

function Get-FilterValue {
    param([string[]]$Arguments)
    $a = @($Arguments)
    $index = [Array]::IndexOf($a, "--filter")
    if ($index -lt 0) { return "" }
    return $a[$index + 1]
}

function Invoke-FakeDocker {
    param([string[]]$Arguments, $Ctx)
    $a = @($Arguments)
    $script:Fake.Calls.Add(($a -join " "))
    $head = "$($a[0]) $($a[1])"
    if ($a[0] -eq "version") { return (New-FakeResult -Stdout "27.0.0") }
    if ($head -eq "context show") { return (New-FakeResult -Stdout "desktop-linux") }
    if ($head -eq "context inspect") { return (New-FakeResult -Stdout $script:Fake.Endpoint) }
    if ($head -eq "compose version") { return (New-FakeResult -Stdout "v2") }
    if ($head -eq "image ls" -and (Get-FilterValue -Arguments $a) -eq "reference=redis:7.4.12") {
        $lines = @($script:Fake.RedisImageDigests | ForEach-Object { "redis|7.4.12|$_" })
        return (New-FakeResult -Stdout ($lines -join "`n"))
    }
    if ($head -eq "image ls") {
        $lines = @($script:Fake.ImageDigests | ForEach-Object { "mariadb|11.8.8|$_" })
        return (New-FakeResult -Stdout ($lines -join "`n"))
    }
    if ($a[0] -eq "compose" -and $a -contains "config") {
        if ($script:Fake.ConfigFails) { return (New-FakeResult -ExitCode 1 -Stderr "compose config falló") }
        return (New-FakeResult -Stdout (New-FakeRenderedConfig -Ctx $Ctx -Override (Get-FakeOverride -Arguments $a)))
    }
    if ($a[0] -eq "compose" -and $a -contains "up") {
        $override = Get-FakeOverride -Arguments $a
        $names = $Ctx.Names
        $labels = Get-BaseLabels -RunId $names.RunId -InvocationId $override.Invocation -OwnerProof $override.Proof
        if ($override.RedisImage -and $script:Fake.ComposeMode -eq "ok") {
            switch ($script:Fake.RedisUpMode) {
                "ok" { [void](Add-FakeContainer -Name $names.RedisContainerName -Labels $labels -Hostname $names.RedisContainerHostname -Mounts $script:Fake.RedisMounts) }
                "foreign" { [void](Add-FakeContainer -Name $names.RedisContainerName -Labels (Get-BaseLabels -RunId $names.RunId -InvocationId ("f" * 32)) -Hostname $names.RedisContainerHostname) }
                default { }
            }
        }
        switch ($script:Fake.ComposeMode) {
            "ok" {
                [void](Add-FakeNetwork -Name $names.NetworkName -Labels $labels)
                [void](Add-FakeContainer -Name $names.ContainerName -Labels $labels -Hostname $names.ContainerHostname -Mounts $script:Fake.NewContainerMounts)
                return (New-FakeResult)
            }
            "networkOnly" {
                [void](Add-FakeNetwork -Name $names.NetworkName -Labels $labels)
                return (New-FakeResult -ExitCode 1 -Stderr "port is already allocated")
            }
            "timeout" {
                [void](Add-FakeNetwork -Name $names.NetworkName -Labels $labels)
                [void](Add-FakeContainer -Name $names.ContainerName -Labels $labels -Hostname $names.ContainerHostname)
                return (New-FakeResult -TimedOut)
            }
            "foreignRace" {
                [void](Add-FakeNetwork -Name $names.NetworkName -Labels $labels)
                [void](Add-FakeContainer -Name $names.ContainerName -Labels (Get-BaseLabels -RunId $names.RunId -InvocationId ("f" * 32)) -Hostname $names.ContainerHostname)
                return (New-FakeResult)
            }
            default { return (New-FakeResult -ExitCode 1 -Stderr "compose falló") }
        }
    }
    if ($head -eq "container inspect" -and $a -contains "--format") { return (New-FakeResult -Stdout $script:Fake.Health) }
    if ($head -eq "container inspect") {
        $c = Find-FakeResource -Table $script:Fake.Containers -Ref $a[2]
        if ($null -eq $c) { return (New-FakeResult -ExitCode 1 -Stderr "Error: No such container: $($a[2])") }
        $envList = @("MARIADB_PASSWORD=$FakeDbSecret", "MARIADB_ROOT_PASSWORD=$FakeRootSecret", "MARIADB_DATABASE=x")
        $mounts = @($c.Mounts | ForEach-Object { [ordered]@{ Type = $_.Type; Name = $_.Name; Destination = $_.Destination } })
        $obj = [ordered]@{ Id = $c.Id; Name = "/" + $c.Name; Config = [ordered]@{ Labels = $c.Labels; Hostname = $c.Hostname; Env = $envList }; Mounts = $mounts }
        return (New-FakeResult -Stdout (ConvertTo-Json -InputObject @($obj) -Depth 6))
    }
    if ($head -eq "network inspect") {
        $n = Find-FakeResource -Table $script:Fake.Networks -Ref $a[2]
        if ($null -eq $n) { return (New-FakeResult -ExitCode 1 -Stderr "Error: No such network: $($a[2])") }
        $obj = [ordered]@{ Id = $n.Id; Name = $n.Name; Labels = $n.Labels }
        return (New-FakeResult -Stdout (ConvertTo-Json -InputObject @($obj) -Depth 6))
    }
    if (($head -eq "container ls" -or $head -eq "network ls") -and $a -contains "-q") {
        # Búsqueda por etiqueta de RunId (preexistencia).
        $filter = Get-FilterValue -Arguments $a
        $runId = $filter.Substring($filter.IndexOf("=", 6) + 1)
        $table = $script:Fake.Networks
        if ($a[0] -eq "container") { $table = $script:Fake.Containers }
        $ids = @($table.Values | Where-Object { $_.Labels["agrotech.run-id"] -eq $runId } | ForEach-Object { $_.Id })
        return (New-FakeResult -Stdout ($ids -join "`n"))
    }
    if ($head -eq "container ls" -or $head -eq "network ls") {
        if ($script:Fake.LsFailure) { return (New-FakeResult -ExitCode 1 -Stderr $script:Fake.LsFailure) }
        $filter = Get-FilterValue -Arguments $a
        $key = $filter.Substring(0, $filter.IndexOf("="))
        $value = $filter.Substring($filter.IndexOf("=") + 1)
        $table = $script:Fake.Networks
        if ($a[0] -eq "container") { $table = $script:Fake.Containers }
        $rows = @()
        foreach ($item in $table.Values) {
            $hit = $false
            if ($key -eq "id") { $hit = $item.Id.StartsWith($value) }
            elseif ($key -eq "name") { $hit = $item.Name.Contains($value) }
            elseif ($key -eq "volume") { $hit = (@($item.Mounts | Where-Object { $_.Name -eq $value }).Count -gt 0) }
            if (-not $hit) { continue }
            if ($a[$a.Count - 1] -eq "{{.ID}}") { $rows += $item.Id } else { $rows += ("{0}|{1}" -f $item.Id, $item.Name) }
        }
        return (New-FakeResult -Stdout ($rows -join "`n"))
    }
    if ($head -eq "volume ls") {
        if ($script:Fake.VolumeLsFailure) { return (New-FakeResult -ExitCode 1 -Stderr $script:Fake.VolumeLsFailure) }
        $value = (Get-FilterValue -Arguments $a).Substring(5)
        $rows = @($script:Fake.Volumes.Keys | Where-Object { $_.Contains($value) })
        return (New-FakeResult -Stdout ($rows -join "`n"))
    }
    if ($head -eq "volume inspect") {
        if (-not $script:Fake.Volumes.ContainsKey($a[2])) { return (New-FakeResult -ExitCode 1 -Stderr "Error: No such volume: $($a[2])") }
        $v = $script:Fake.Volumes[$a[2]]
        return (New-FakeResult -Stdout (ConvertTo-Json -InputObject @([ordered]@{ Name = $v.Name; Labels = $v.Labels }) -Depth 6))
    }
    if ($head -eq "volume rm") {
        if ($script:Fake.VolumeRmFails) { return (New-FakeResult -ExitCode 1 -Stderr "volume rm falló") }
        $script:Fake.Volumes.Remove($a[2])
        return (New-FakeResult)
    }
    if ($a[0] -eq "port" -and $a[2] -eq "6379/tcp") { return (New-FakeResult -Stdout $script:Fake.RedisPort) }
    if ($a[0] -eq "port") { return (New-FakeResult -Stdout $script:Fake.Port) }
    if ($a[0] -eq "exec" -and (($a -join " ") -match 'redis-cli')) {
        $script:Fake.ExecArgs.Add(($a -join " "))
        if ($a -contains "SET") { return (New-FakeResult -Stdout $script:Fake.RedisSet) }
        return (New-FakeResult -Stdout $script:Fake.RedisPing)
    }
    if ($a[0] -eq "exec") {
        $script:Fake.ExecArgs.Add(($a -join " "))
        $escaped = $Ctx.Names.DatabaseName.Replace("_", "\_")
        return (New-FakeResult -Stdout (($script:Fake.Grants -join "`n").Replace("__DB__", $escaped)))
    }
    if ($head -eq "container rm") {
        if ($script:Fake.RmFails) { return (New-FakeResult -ExitCode 1 -Stderr "rm falló MARIADB_PASSWORD=$FakeDbSecret") }
        $script:Fake.Containers.Remove($a[3])
        if ($script:Fake.RmUnconfirmed) { return (New-FakeResult -Unconfirmed -Survivors @(4242)) }
        return (New-FakeResult)
    }
    if ($head -eq "network rm") {
        if ($script:Fake.RmFails) { return (New-FakeResult -ExitCode 1 -Stderr "rm falló") }
        $script:Fake.Networks.Remove($a[2])
        return (New-FakeResult)
    }
    return (New-FakeResult -ExitCode 99 -Stderr "comando no simulado")
}

function New-TestOptions {
    param([hashtable]$Extra = @{})
    $options = @{
        Root = $script:FakeRoot; CleanupOnly = $false; RunId = ""; Validate = $false; SqlMigrateOnly = $false
        Tests = @("apps/farms/tests"); TestsBound = $false; SettingsModule = "config.settings_test_disposable"
        SettingsModuleBound = $false
        OutputDirectory = (Join-Path $script:OutBase ("out-" + [guid]::NewGuid().ToString("N")))
        StateFile = $null; HealthTimeoutSeconds = 10; MariaDbImageDigest = $TestDigest
    }
    foreach ($key in $Extra.Keys) { $options[$key] = $Extra[$key] }
    return $options
}

function New-TestStateFile {
    <#
    run-state.json de prueba. Con InvocationId se genera AUTÉNTICO con el
    Save-RunState real (DPAPI + HMAC); con -NoIntegrity o sin InvocationId, un
    estado sin bloque de integridad (como el de versiones anteriores).
    #>
    param([string]$RunId, [string]$InvocationId, [string]$ContainerId, [string]$NetworkId, [string[]]$Volumes = @(), [switch]$NoIntegrity)
    $dir = Join-Path $TestDrive ("state-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $dir -Force | Out-Null
    $path = Join-Path $dir "run-state.json"
    if (-not $InvocationId -or $NoIntegrity) {
        $payload = [ordered]@{ RunId = $RunId; Owned = [ordered]@{ ContainerId = $ContainerId; NetworkId = $NetworkId } }
        if ($InvocationId) { $payload["InvocationId"] = $InvocationId }
        Set-Content -LiteralPath $path -Value ($payload | ConvertTo-Json -Depth 4) -Encoding UTF8
        return $path
    }
    $secret = Get-TestSecret -InvocationId $InvocationId
    $history = New-Object System.Collections.Generic.List[object]
    $history.Add(@{ State = "NOT_STARTED"; At = "2026-01-01T00:00:00Z"; Detail = "" })
    $ctx = @{
        StateFile = $path; Names = (Get-DisposableNames -Id $RunId); InvocationId = $InvocationId; Mode = "Validate"
        SettingsModule = "config.settings_test_disposable"; Tests = @(); DockerContext = "desktop-linux"
        Owned = @{ ContainerId = $ContainerId; NetworkId = $NetworkId; Volumes = @($Volumes) }
        State = "CLEANUP_FAILED"; StageCode = 0; StageMessage = ""; Cleanup = $null; ExitCode = 41
        History = $history; TerminationFailures = (New-Object System.Collections.Generic.List[object])
        OwnerToken = $secret.Token; ProtectedToken = $secret.Protected
    }
    Save-RunState -Ctx $ctx
    return $path
}

function Invoke-TestOrchestrator {
    param([hashtable]$Options)
    return [int](@(Invoke-DisposableOrchestrator -Options $Options)[-1])
}

function Get-TestState {
    param([hashtable]$Options)
    return (Get-Content -LiteralPath (Join-Path $Options.OutputDirectory "run-state.json") -Raw -Encoding UTF8 | ConvertFrom-Json)
}

function Register-OrchestratorMocks {
    Mock Get-DockerExecutable { "C:\fake\docker.exe" }
    Mock Invoke-DockerCommand { Invoke-FakeDocker -Arguments $Arguments -Ctx $Ctx }
    Mock Invoke-PythonChild {
        $script:Fake.PythonCalls.Add($Step)
        $script:Fake.PythonArgs.Add(($Arguments -join " "))
        if ($null -ne $script:Fake.PythonHook) { & $script:Fake.PythonHook $Step }
        $code = 0
        if ($script:Fake.PythonCodes.ContainsKey($Step)) { $code = $script:Fake.PythonCodes[$Step] }
        if ($script:Fake.PythonUnconfirmed.ContainsKey($Step)) { New-FakeResult -ExitCode $code -Unconfirmed -Survivors @(5151) }
        else { New-FakeResult -ExitCode $code }
    }
    Mock Test-LoopbackPortFree { $script:Fake.PortFree }
    Mock Enter-OrchestratorLock { $true }
    Mock Exit-OrchestratorLock { }
    Mock Start-Sleep { }
}

# ===========================================================================

Describe "Tabla central de códigos de salida" {
    It "coincide con config/disposable_preflight.py" {
        $py = Get-Content -LiteralPath (Join-Path $RepoRoot "config\disposable_preflight.py") -Raw -Encoding UTF8
        $block = ([regex]::Match($py, 'EXIT_CODES = \{(?<body>[^}]+)\}')).Groups['body'].Value
        $pairs = [regex]::Matches($block, '"(?<name>[A-Z_]+)":\s*(?<value>\d+)')
        $pairs.Count | Should Be 4
        foreach ($pair in $pairs) {
            (Get-DisposableExitCode $pair.Groups['name'].Value) | Should Be ([int]$pair.Groups['value'].Value)
        }
    }

    It "está documentada en la ayuda del script principal" {
        $script = Get-Content -LiteralPath (Join-Path $RepoRoot "tools\run_permissions_disposable.ps1") -Raw -Encoding UTF8
        foreach ($name in $script:DisposableExitCodes.Keys) {
            $value = $script:DisposableExitCodes[$name]
            $script | Should Match ("(?m)^\s+{0}\s+{1}\s" -f $value, $name)
        }
    }

    It "combina etapa, limpieza e interrupción" {
        Get-FinalExitCode -StageCode 0 -CleanupRequired $true -CleanupOk $true -Interrupted $false | Should Be 0
        Get-FinalExitCode -StageCode 10 -CleanupRequired $true -CleanupOk $true -Interrupted $false | Should Be 10
        Get-FinalExitCode -StageCode 20 -CleanupRequired $true -CleanupOk $true -Interrupted $false | Should Be 20
        Get-FinalExitCode -StageCode 30 -CleanupRequired $true -CleanupOk $true -Interrupted $false | Should Be 30
        Get-FinalExitCode -StageCode 0 -CleanupRequired $true -CleanupOk $false -Interrupted $false | Should Be 41
        Get-FinalExitCode -StageCode 30 -CleanupRequired $true -CleanupOk $false -Interrupted $false | Should Be 42
        Get-FinalExitCode -StageCode 50 -CleanupRequired $false -CleanupOk $true -Interrupted $false | Should Be 50
        Get-FinalExitCode -StageCode 0 -CleanupRequired $true -CleanupOk $true -Interrupted $true | Should Be 43
        Get-FinalExitCode -StageCode 0 -CleanupRequired $true -CleanupOk $false -Interrupted $true | Should Be 43
    }

    It "solo anuncia 'validado y eliminado' con código 0 en -Validate" {
        Get-FinalMessage -Code 0 -Mode Validate | Should Be "Entorno desechable validado y eliminado."
        foreach ($code in @(10, 20, 30, 40, 41, 42, 43, 50)) {
            Get-FinalMessage -Code $code -Mode Validate | Should Not Match "validado y eliminado"
        }
        Get-FinalMessage -Code 0 -Mode EnvironmentOnly | Should Not Match "validado"
    }
}

Describe "SettingsModule" {
    $script:FakeRoot = New-FakeRoot -Base $TestDrive
    $script:OutBase = $TestDrive
    BeforeEach { $script:Fake = New-FakeDockerState }
    Register-OrchestratorMocks

    It "acepta las configuraciones desechables existentes" {
        foreach ($module in @("config.settings_test_disposable", "config.settings_test_disposable_isolated")) {
            $check = Test-OrchestratorOptions -Options (New-TestOptions @{ SettingsModule = $module; SettingsModuleBound = $true })
            $check.Ok | Should Be $true
            $check.Normalized.SettingsModule | Should BeExactly $module
            Test-Path -LiteralPath (Join-Path $RepoRoot ($module.Replace(".", "\") + ".py")) | Should Be $true
        }
    }

    It "usa la configuración desechable por defecto cuando está ausente" {
        $check = Test-OrchestratorOptions -Options (New-TestOptions @{ SettingsModule = $null })
        $check.Ok | Should Be $true
        $check.Normalized.SettingsModule | Should BeExactly "config.settings_test_disposable"
    }

    It "rechaza configuraciones no permitidas, vacías o de producción" {
        foreach ($module in @("config.settings", "config.settings_test", "CONFIG.SETTINGS_TEST_DISPOSABLE", "config.settings_test_disposable;x", "")) {
            $check = Test-OrchestratorOptions -Options (New-TestOptions @{ SettingsModule = $module; SettingsModuleBound = $true })
            $check.Ok | Should Be $false
        }
    }

    It "rechaza config.settings antes de tocar Docker o Python (código 3)" {
        $code = Invoke-TestOrchestrator -Options (New-TestOptions @{ Validate = $true; SettingsModule = "config.settings"; SettingsModuleBound = $true })
        $code | Should Be 3
        Assert-MockCalled Invoke-DockerCommand -Times 0 -Exactly -Scope It
        Assert-MockCalled Invoke-PythonChild -Times 0 -Exactly -Scope It
    }
}

Describe "Parámetros y combinaciones" {
    $script:FakeRoot = New-FakeRoot -Base $TestDrive
    $script:OutBase = $TestDrive
    BeforeEach { $script:Fake = New-FakeDockerState }
    Register-OrchestratorMocks

    $cases = @(
        @{ Name = "Validate y SqlMigrateOnly"; Extra = @{ Validate = $true; SqlMigrateOnly = $true } },
        @{ Name = "Tests sin Validate"; Extra = @{ TestsBound = $true } },
        @{ Name = "CleanupOnly con Validate"; Extra = @{ CleanupOnly = $true; Validate = $true; RunId = "0123456789ab"; StateFile = "C:\x.json" } },
        @{ Name = "CleanupOnly sin StateFile"; Extra = @{ CleanupOnly = $true; RunId = "0123456789ab"; OutputDirectory = $null } },
        @{ Name = "CleanupOnly sin RunId"; Extra = @{ CleanupOnly = $true; StateFile = "C:\x.json"; OutputDirectory = $null } },
        @{ Name = "StateFile sin CleanupOnly"; Extra = @{ StateFile = "C:\x.json" } },
        @{ Name = "RunId inválido"; Extra = @{ RunId = "ABC" } },
        @{ Name = "RunId en mayúsculas"; Extra = @{ RunId = "0123456789AB" } },
        @{ Name = "SqlMigrateOnly con configuración no aislada"; Extra = @{ SqlMigrateOnly = $true; SettingsModuleBound = $true } },
        @{ Name = "SqlMigrateOnly con Tests"; Extra = @{ SqlMigrateOnly = $true; TestsBound = $true } },
        @{ Name = "timeout fuera de rango"; Extra = @{ PytestTimeoutSeconds = 5 } },
        @{ Name = "timeout no numérico"; Extra = @{ HealthTimeoutSeconds = "diez" } }
    )
    foreach ($case in $cases) {
        It "rechaza: $($case.Name) (código 3, sin recursos)" {
            $code = Invoke-TestOrchestrator -Options (New-TestOptions $case.Extra)
            $code | Should Be 3
            Assert-MockCalled Invoke-DockerCommand -Times 0 -Exactly -Scope It
        }
    }

    It "fuerza la configuración aislada en -SqlMigrateOnly" {
        $check = Test-OrchestratorOptions -Options (New-TestOptions @{ SqlMigrateOnly = $true })
        $check.Ok | Should Be $true
        $check.Normalized.SettingsModule | Should BeExactly "config.settings_test_disposable_isolated"
    }
}

Describe "Validación de -Tests" {
    $script:FakeRoot = New-FakeRoot -Base $TestDrive
    $outside = Join-Path $TestDrive "fuera"
    New-Item -ItemType Directory -Path $outside -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $outside "test_x.py") -Value "" -Encoding ASCII
    $junction = Join-Path $script:FakeRoot "apps\farms\tests\enlace"
    New-Item -ItemType Junction -Path $junction -Target $outside | Out-Null
    $canSymlink = $true
    try {
        New-Item -ItemType SymbolicLink -Path (Join-Path $script:FakeRoot "apps\farms\tests\simbolico") -Target $outside -ErrorAction Stop | Out-Null
    }
    catch { $canSymlink = $false }

    It "acepta un directorio y un archivo de pruebas internos" {
        Resolve-AuthorizedTestPath -Root $script:FakeRoot -Item "apps/farms/tests" | Should Be "apps\farms\tests"
        Resolve-AuthorizedTestPath -Root $script:FakeRoot -Item "apps\farms\tests\test_a.py" | Should Be "apps\farms\tests\test_a.py"
    }

    $rejected = @(
        "C:\Windows\System32", "$outside\test_x.py", "..\..\fuera", "apps/../conftest.py", "apps/farms/tests/../../../x",
        "apps/farms/tests/no_existe.py", "-p", "-pno:cacheprovider", "--ds=config.settings_test", "-o", "--junitxml=C:\x.xml",
        "--create-db", "-k", " -x", "apps/farms/tests::test_x", "apps/farms/tests/*", "apps/farms/other/test_b.py",
        "apps/farms/tests/helper.py", "tests", "apps", "", "   "
    )
    foreach ($item in $rejected) {
        It "rechaza '$item'" {
            { Resolve-AuthorizedTestPath -Root $script:FakeRoot -Item $item } | Should Throw
        }
    }

    It "rechaza una junction dentro de apps\...\tests que apunta fuera del proyecto" {
        { Resolve-AuthorizedTestPath -Root $script:FakeRoot -Item "apps/farms/tests/enlace/test_x.py" } | Should Throw
        { Resolve-AuthorizedTestPath -Root $script:FakeRoot -Item "apps/farms/tests/enlace" } | Should Throw
    }

    It "rechaza un symlink dentro de apps\...\tests" -Skip:(-not $canSymlink) {
        { Resolve-AuthorizedTestPath -Root $script:FakeRoot -Item "apps/farms/tests/simbolico/test_x.py" } | Should Throw
    }

    It "los argumentos de pytest controlados no pueden ser reemplazados por -Tests" {
        $ctx = @{ SettingsModule = "config.settings_test_disposable"; OutputDirectory = $outside; Tests = @("apps\farms\tests") }
        $list = @(Get-PytestArguments -Ctx $ctx)
        $list[0..1] -join " " | Should Be "-m pytest"
        ($list -join " ") | Should Match "-p config\.disposable_preflight -p no:cacheprovider -o addopts= --ds=config\.settings_test_disposable --reuse-db --junitxml="
        $list[$list.Count - 1] | Should Be "apps\farms\tests"
        @($list | Where-Object { $_ -like "--ds=*" }).Count | Should Be 1
    }
}

Describe "Validación de OutputDirectory" {
    $script:FakeRoot = New-FakeRoot -Base $TestDrive
    $toRepo = Join-Path $TestDrive "hacia-repo"
    New-Item -ItemType Junction -Path $toRepo -Target $script:FakeRoot | Out-Null
    $nonEmpty = Join-Path $TestDrive "no-vacio"
    New-Item -ItemType Directory -Path $nonEmpty -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $nonEmpty "x.txt") -Value "x" -Encoding ASCII

    It "acepta un directorio nuevo fuera del repositorio" {
        $target = Join-Path $TestDrive "salida-nueva"
        Resolve-SafeOutputDirectory -Root $script:FakeRoot -Path $target | Should Be $target
        Test-Path -LiteralPath $target | Should Be $false
    }

    $bad = @{
        "dentro del repositorio" = (Join-Path $script:FakeRoot "resultados")
        "igual al repositorio" = $script:FakeRoot
        "que contiene el repositorio" = $TestDrive
        "relativo" = "resultados"
        "junction hacia el repositorio" = (Join-Path $toRepo "resultados")
        "existente y no vacío" = $nonEmpty
        "raíz de unidad" = "C:\"
        "ubicación del sistema" = (Join-Path $env:windir "Temp\agrotech")
        "carpeta compartida" = $env:LOCALAPPDATA
    }
    foreach ($key in $bad.Keys) {
        $path = $bad[$key]
        It "rechaza OutputDirectory $key" {
            { Resolve-SafeOutputDirectory -Root $script:FakeRoot -Path $path } | Should Throw
        }
    }

    It "el orquestador devuelve 3 con OutputDirectory dentro del repositorio" {
        $script:OutBase = $TestDrive
        $script:Fake = New-FakeDockerState
        Register-OrchestratorMocks
        $code = Invoke-TestOrchestrator -Options (New-TestOptions @{ OutputDirectory = (Join-Path $script:FakeRoot "out") })
        $code | Should Be 3
        Test-Path -LiteralPath (Join-Path $script:FakeRoot "out") | Should Be $false
    }
}

Describe "Entorno de procesos hijos" {
    $source = @{
        PATH = $env:PATH; SystemRoot = $env:SystemRoot; TEMP = $env:TEMP
        COMPOSE_FILE = "x.yml"; COMPOSE_PROJECT_NAME = "otro"; COMPOSE_ENV_FILES = "a"
        DOCKER_HOST = "tcp://10.0.0.1:2375"; DOCKER_CONTEXT = "remoto"; DOCKER_CONFIG = "C:\otro"
        PYTEST_ADDOPTS = "--ds=config.settings_test --cov=apps"; PYTEST_PLUGINS = "malicioso"
        PYTHONPATH = "C:\malicioso"; DJANGO_SETTINGS_MODULE = "config.settings"; DJANGO_SECRET_KEY = "k"
        SMTP_USER = "usuario-real"; SMTP_HOST = "smtp.real"; SMTP_PASSWORD = "p"; SMTP_FROM = "a@b.c"
        BREVO_API_KEY = "clave"; DATABASE_URL = "postgres://x"; DB_NAME = "agrotech_t"; TEST_DB_PASSWORD = "p"
    }
    $names = Get-DisposableNames -Id "0123456789ab"

    It "el entorno base solo contiene la lista blanca" {
        $base = Get-BaseChildEnvironment -Source $source
        @($base.Keys | Sort-Object) -join "," | Should Be "PATH,SystemRoot,TEMP"
    }

    It "Docker no hereda COMPOSE_*, DOCKER_*, PYTEST_*, SMTP ni credenciales" {
        $envMap = New-DockerChildEnvironment -Names $names -DbPassword "db-secreto" -RootPassword "root-secreto" -Source $source
        foreach ($key in $envMap.Keys) {
            $key | Should Not Match '^(COMPOSE_|DOCKER_|PYTEST_|PYTHON|DJANGO_|SMTP_|BREVO_|TEST_DB_|DB_|DATABASE_URL)'
        }
        $envMap["DISPOSABLE_DB_NAME"] | Should Be "agrotech_permtest_0123456789ab"
    }

    It "Python recibe .env desactivado, correo vacío y la configuración autorizada" {
        $envMap = New-PythonChildEnvironment -Names $names -SettingsModule "config.settings_test_disposable" -DbPassword "db-secreto" -ReportDirectory "C:\out" -Source $source
        $envMap["PYTHON_DOTENV_DISABLED"] | Should Be "1"
        $envMap["PYTHONDONTWRITEBYTECODE"] | Should Be "1"
        $envMap["DJANGO_SETTINGS_MODULE"] | Should BeExactly "config.settings_test_disposable"
        foreach ($name in @("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM", "BREVO_API_KEY", "DATABASE_URL", "REDIS_URL")) {
            $envMap.ContainsKey($name) | Should Be $true
            $envMap[$name] | Should BeExactly ""
        }
        foreach ($name in @("PYTEST_ADDOPTS", "PYTEST_PLUGINS", "PYTHONPATH", "COMPOSE_FILE", "DOCKER_HOST", "DJANGO_SECRET_KEY", "DB_NAME", "TEST_DB_PASSWORD", "DISPOSABLE_ROOT_PASSWORD")) {
            $envMap.ContainsKey($name) | Should Be $false
        }
    }

    It "un proceso real recibe las variables vacías y no las heredadas" -Skip:(-not (Test-Path -LiteralPath $RepoPython)) {
        $before = @(Get-ChildItem Env: | ForEach-Object { "$($_.Name)=$($_.Value)" }) -join "`n"
        $envMap = New-PythonChildEnvironment -Names $names -SettingsModule "config.settings_test_disposable" -DbPassword "db-secreto" -ReportDirectory "C:\out" -Source $source
        $code = "import os, json; keys=['SMTP_USER','SMTP_HOST','BREVO_API_KEY','PYTEST_ADDOPTS','PYTEST_PLUGINS','DOCKER_HOST','COMPOSE_FILE','PYTHON_DOTENV_DISABLED','DJANGO_SETTINGS_MODULE']; print(json.dumps({k: os.environ.get(k) for k in keys}))"
        $r = Invoke-BoundedProcess -FilePath $RepoPython -ArgumentList @("-I", "-B", "-c", $code) -TimeoutSeconds 30 -Environment $envMap -WorkingDirectory $TestDrive
        $r.ExitCode | Should Be 0
        $seen = $r.Stdout | ConvertFrom-Json
        $seen.SMTP_USER | Should BeExactly ""
        $seen.SMTP_HOST | Should BeExactly ""
        $seen.BREVO_API_KEY | Should BeExactly ""
        $seen.PYTEST_ADDOPTS | Should BeNullOrEmpty
        $seen.PYTEST_PLUGINS | Should BeNullOrEmpty
        $seen.DOCKER_HOST | Should BeNullOrEmpty
        $seen.COMPOSE_FILE | Should BeNullOrEmpty
        $seen.PYTHON_DOTENV_DISABLED | Should Be "1"
        $seen.DJANGO_SETTINGS_MODULE | Should BeExactly "config.settings_test_disposable"
        $after = @(Get-ChildItem Env: | ForEach-Object { "$($_.Name)=$($_.Value)" }) -join "`n"
        $after | Should BeExactly $before
    }

    It "los procesos Python del orquestador usan -B -E -s" {
        $script:Fake = New-FakeDockerState
        $ctx = @{ Python = "python.exe"; PythonEnvironment = @{}; Root = $TestDrive; Secrets = @(); LogDirectory = $null }
        Mock Invoke-BoundedProcess { New-FakeResult }
        [void](Invoke-PythonChild -Ctx $ctx -Step "x" -Arguments @("-m", "config.disposable_preflight") -TimeoutSeconds 5)
        Assert-MockCalled Invoke-BoundedProcess -Times 1 -Exactly -ParameterFilter { ($ArgumentList[0..3] -join " ") -eq "-B -E -s -X" }
    }

    It "el subcomando de migración pasa siempre por config.disposable_preflight" {
        $ctx = @{ SettingsModule = "config.settings_test_disposable"; Names = $names }
        $list = @(Get-PreflightArguments -Ctx $ctx -Command "migrate")
        ($list[0..2] -join " ") | Should Be "-m config.disposable_preflight migrate"
        ($list -join " ") | Should Not Match "manage\.py"
    }
}

Describe "Procesos acotados: timeout, terminación y citado" {
    $baseEnv = Get-BaseChildEnvironment

    It "cita argumentos con espacios, comillas y barras invertidas" -Skip:(-not (Test-Path -LiteralPath $RepoPython)) {
        $values = @("a b", 'c"d', 'e\', 'f\"g', "", "C:\Program Files\x y\", "--x=1")
        $code = "import sys, json; print(json.dumps(sys.argv[1:]))"
        $r = Invoke-BoundedProcess -FilePath $RepoPython -ArgumentList (@("-I", "-B", "-c", $code) + $values) -TimeoutSeconds 30 -Environment $baseEnv -WorkingDirectory $TestDrive
        $r.ExitCode | Should Be 0
        # En PS 5.1, ConvertFrom-Json por tubería emite el array como un único objeto.
        $seen = ConvertFrom-Json -InputObject $r.Stdout
        $seen.Count | Should Be $values.Count
        for ($i = 0; $i -lt $values.Count; $i++) { $seen[$i] | Should BeExactly $values[$i] }
    }

    It "captura el código de salida de un proceso que termina" -Skip:(-not (Test-Path -LiteralPath $RepoPython)) {
        $r = Invoke-BoundedProcess -FilePath $RepoPython -ArgumentList @("-I", "-B", "-c", "import sys; print('hola'); sys.exit(7)") -TimeoutSeconds 30 -Environment $baseEnv -WorkingDirectory $TestDrive
        $r.ExitCode | Should Be 7
        $r.TimedOut | Should Be $false
        $r.Stdout.Trim() | Should Be "hola"
    }

    It "redacta secretos en la salida" -Skip:(-not (Test-Path -LiteralPath $RepoPython)) {
        $r = Invoke-BoundedProcess -FilePath $RepoPython -ArgumentList @("-I", "-B", "-c", "print('clave=abc123secreto')") -TimeoutSeconds 30 -Environment $baseEnv -WorkingDirectory $TestDrive -Redact @("abc123secreto")
        $r.Stdout | Should Not Match "abc123secreto"
        $r.Stdout | Should Match "<redactado>"
    }

    It "termina por timeout y confirma la terminación" {
        $r = Invoke-BoundedProcess -FilePath $PowerShellExe -ArgumentList @("-NoProfile", "-Command", "Start-Sleep -Seconds 60") -TimeoutSeconds 2 -Environment $baseEnv -WorkingDirectory $TestDrive
        $r.TimedOut | Should Be $true
        $r.ExitCode | Should BeNullOrEmpty
        $r.TerminationConfirmed | Should Be $true
        @($r.Survivors).Count | Should Be 0
        $r.DurationMs | Should BeLessThan 30000
    }

    It "termina también un nieto que no termina por sí mismo" {
        $pidFile = Join-Path $TestDrive "nieto.pid"
        $command = "`$p = Start-Process -FilePath '$PowerShellExe' -ArgumentList '-NoProfile','-Command','Start-Sleep -Seconds 120' -PassThru -WindowStyle Hidden; Set-Content -LiteralPath '$pidFile' -Value `$p.Id; Start-Sleep -Seconds 120"
        $r = Invoke-BoundedProcess -FilePath $PowerShellExe -ArgumentList @("-NoProfile", "-Command", $command) -TimeoutSeconds 10 -Environment $baseEnv -WorkingDirectory $TestDrive
        $r.TimedOut | Should Be $true
        $r.TerminationConfirmed | Should Be $true
        Test-Path -LiteralPath $pidFile | Should Be $true
        $grandchild = [int](Get-Content -LiteralPath $pidFile -Raw).Trim()
        Get-Process -Id $grandchild -ErrorAction SilentlyContinue | Should BeNullOrEmpty
    }

    It "no declara terminación confirmada si el proceso sobrevive" {
        # Sin terminar el Job: el hijo sigue vivo durante la comprobación; al
        # cerrarse el Job (KILL_ON_JOB_CLOSE) muere igualmente.
        Mock Stop-ProcessTree { }
        $r = Invoke-BoundedProcess -FilePath $PowerShellExe -ArgumentList @("-NoProfile", "-Command", "Start-Sleep -Seconds 60") -TimeoutSeconds 2 -Environment $baseEnv -WorkingDirectory $TestDrive -TerminationGraceSeconds 1
        $r.TimedOut | Should Be $true
        $r.TerminationConfirmed | Should Be $false
        @($r.Survivors).Count | Should BeGreaterThan 0
        foreach ($id in @($r.Survivors)) { Stop-Process -Id $id -Force -ErrorAction SilentlyContinue }
    }
}

Describe "Privilegios en el contenedor (SHOW GRANTS)" {
    $db = "agrotech_permtest_0123456789ab"
    $usage = "GRANT USAGE ON *.* TO ``agrotech_perm_validation``@``%`` IDENTIFIED BY PASSWORD '*H'"
    $all = "GRANT ALL PRIVILEGES ON ``agrotech\_permtest\_0123456789ab``.* TO ``agrotech_perm_validation``@``%``"
    It "acepta USAGE global y ALL sobre la base desechable" {
        Test-GrantOutput -Lines @($usage, $all, "ROLE:NONE", "ROLES:0") -Database $db | Should BeNullOrEmpty
    }
    It "acepta el escape duplicado \\_ de mariadb --batch sin --raw" {
        $doubled = "GRANT ALL PRIVILEGES ON ``agrotech\\_permtest\\_0123456789ab``.* TO ``agrotech_perm_validation``@``%``"
        Test-GrantOutput -Lines @($usage, $doubled, "ROLE:NONE", "ROLES:0") -Database $db | Should BeNullOrEmpty
    }
    It "decodifica \_ y \\_ al mismo nombre y rechaza comodines y formatos desconocidos" {
        (ConvertFrom-GrantSchemaTarget -Target "``agrotech\_permtest\_0123456789ab``.*").Schema | Should BeExactly $db
        (ConvertFrom-GrantSchemaTarget -Target "``agrotech\\_permtest\\_0123456789ab``.*").Schema | Should BeExactly $db
        (ConvertFrom-GrantSchemaTarget -Target "``agrotech\_permtest\_0123456789ab``.``farms``").Schema | Should BeExactly $db
        foreach ($bad in @("``agrotech_permtest_0123456789ab``.*", "``agrotech\_permtest\_%``.*", "``agrotech\_a\b``.*", "agrotech\_x.*", "``x``", "*.*", "FUNCTION", "")) {
            (ConvertFrom-GrantSchemaTarget -Target $bad).Ok | Should Be $false
        }
    }
    $cases = @{
        "privilegio global" = @("GRANT SELECT ON *.* TO ``u``@``%``", $all, "ROLE:NONE", "ROLES:0")
        "GRANT OPTION" = @($usage, ($all + " WITH GRANT OPTION"), "ROLE:NONE", "ROLES:0")
        "otra base" = @($usage, $all, "GRANT SELECT ON ``agrotech_t``.* TO ``u``@``%``", "ROLE:NONE", "ROLES:0")
        "rol concedido" = @($usage, $all, "GRANT ``rol`` TO ``u``@``%``", "ROLE:NONE", "ROLES:0")
        "rol activo" = @($usage, $all, "ROLE:rol", "ROLES:0")
        "roles aplicables" = @($usage, $all, "ROLE:NONE", "ROLES:1")
        "sin verificación de roles" = @($usage, $all)
        "privilegios insuficientes" = @($usage, "GRANT SELECT ON ``agrotech\_permtest\_0123456789ab``.* TO ``u``@``%``", "ROLE:NONE", "ROLES:0")
        "comodín _ sin escapar" = @($usage, "GRANT ALL PRIVILEGES ON ``agrotech_permtest_0123456789ab``.* TO ``u``@``%``", "ROLE:NONE", "ROLES:0")
        "comodín %" = @($usage, "GRANT ALL PRIVILEGES ON ``agrotech\_permtest\_%``.* TO ``u``@``%``", "ROLE:NONE", "ROLES:0")
        "escape desconocido" = @($usage, "GRANT ALL PRIVILEGES ON ``agrotech\_permtest\_0123\a``.* TO ``u``@``%``", "ROLE:NONE", "ROLES:0")
        "SET DEFAULT ROLE" = @($usage, $all, "SET DEFAULT ROLE ``rol`` FOR ``u``@``%``", "ROLE:NONE", "ROLES:0")
        "función" = @($usage, $all, "GRANT EXECUTE ON FUNCTION ``agrotech\_permtest\_0123456789ab``.``f`` TO ``u``@``%``", "ROLE:NONE", "ROLES:0")
        "línea desconocida" = @($usage, $all, "texto arbitrario", "ROLE:NONE", "ROLES:0")
    }
    foreach ($key in $cases.Keys) {
        $lines = $cases[$key]
        It "rechaza: $key" {
            Test-GrantOutput -Lines $lines -Database $db | Should Not BeNullOrEmpty
        }
    }
}

Describe "Propiedad de recursos Docker" {
    $script:FakeRoot = New-FakeRoot -Base $TestDrive
    $script:OutBase = $TestDrive
    BeforeEach { $script:Fake = New-FakeDockerState }
    Register-OrchestratorMocks

    Context "Clasificación de propiedad" {
        $names = Get-DisposableNames -Id "0123456789ab"
        $inv = "a" * 32
        $expected = Get-ExpectedOwnership -Names $names -InvocationId $inv -OwnerProof (Get-TestSecret -InvocationId $inv).Proof
        $make = {
            param($Labels, $Name, $Hostname, $State = "Present")
            [pscustomobject]@{ State = $State; Kind = "container"; Id = "id"; Name = $Name; Labels = $Labels; Hostname = $Hostname; Detail = "" }
        }
        It "Owned con nombre, etiquetas, RunId, InvocationId y hostname" {
            Get-ResourceOwnership -Resource (& $make (Get-BaseLabels $names.RunId $inv) $names.ContainerName $names.ContainerHostname) -Expected $expected | Should Be "Owned"
        }
        It "Foreign con RunId coincidente pero InvocationId distinto" {
            Get-ResourceOwnership -Resource (& $make (Get-BaseLabels $names.RunId ("b" * 32)) $names.ContainerName $names.ContainerHostname) -Expected $expected | Should Be "Foreign"
        }
        It "Foreign si falta o no coincide la prueba de propiedad (AUD-014)" {
            Get-ResourceOwnership -Resource (& $make (Get-BaseLabels $names.RunId $inv -OwnerProof "") $names.ContainerName $names.ContainerHostname) -Expected $expected | Should Be "Foreign"
            Get-ResourceOwnership -Resource (& $make (Get-BaseLabels $names.RunId $inv -OwnerProof ("0" * 64)) $names.ContainerName $names.ContainerHostname) -Expected $expected | Should Be "Foreign"
            $noProof = Get-ExpectedOwnership -Names $names -InvocationId $inv -OwnerProof ""
            Get-ResourceOwnership -Resource (& $make (Get-BaseLabels $names.RunId $inv) $names.ContainerName $names.ContainerHostname) -Expected $noProof | Should Be "Foreign"
        }
        It "Foreign para un entorno antiguo sin InvocationId" {
            Get-ResourceOwnership -Resource (& $make (Get-BaseLabels $names.RunId $null) $names.ContainerName $names.ContainerHostname) -Expected $expected | Should Be "Foreign"
        }
        It "Foreign con otro nombre" {
            Get-ResourceOwnership -Resource (& $make (Get-BaseLabels $names.RunId $inv) "otro" $names.ContainerHostname) -Expected $expected | Should Be "Foreign"
        }
        It "Ambiguous con hostname contradictorio o inspección fallida" {
            Get-ResourceOwnership -Resource (& $make (Get-BaseLabels $names.RunId $inv) $names.ContainerName "otro") -Expected $expected | Should Be "Ambiguous"
            Get-ResourceOwnership -Resource (& $make @{} $null $null "Error") -Expected $expected | Should Be "Ambiguous"
        }
    }

    It "contenedor preexistente: código 50 y nunca se borra" {
        $runId = "aaaaaaaaaaaa"
        $names = Get-DisposableNames -Id $runId
        $id = Add-FakeContainer -Name $names.ContainerName -Labels (Get-BaseLabels $runId $null) -Hostname $names.ContainerHostname
        $code = Invoke-TestOrchestrator -Options (New-TestOptions @{ Validate = $true; RunId = $runId })
        $code | Should Be 50
        $script:Fake.Containers.ContainsKey($id) | Should Be $true
        Assert-MockCalled Invoke-DockerCommand -Times 0 -Exactly -Scope It -ParameterFilter { $Arguments -contains "rm" }
        Assert-MockCalled Invoke-DockerCommand -Times 0 -Exactly -Scope It -ParameterFilter { $Arguments -contains "up" }
    }

    It "red preexistente: código 50 y nunca se borra" {
        $runId = "bbbbbbbbbbbb"
        $names = Get-DisposableNames -Id $runId
        $id = Add-FakeNetwork -Name $names.NetworkName -Labels (Get-BaseLabels $runId ("c" * 32))
        $code = Invoke-TestOrchestrator -Options (New-TestOptions @{ Validate = $true; RunId = $runId })
        $code | Should Be 50
        $script:Fake.Networks.ContainsKey($id) | Should Be $true
        Assert-MockCalled Invoke-DockerCommand -Times 0 -Exactly -Scope It -ParameterFilter { $Arguments -contains "rm" }
    }

    It "recurso con la etiqueta del RunId bajo otro nombre: código 50" {
        $runId = "cccccccccccc"
        [void](Add-FakeContainer -Name "otro-nombre" -Labels (Get-BaseLabels $runId $null) -Hostname "x")
        Invoke-TestOrchestrator -Options (New-TestOptions @{ Validate = $true; RunId = $runId }) | Should Be 50
        Assert-MockCalled Invoke-DockerCommand -Times 0 -Exactly -Scope It -ParameterFilter { $Arguments -contains "up" }
    }

    It "puerto ocupado: código 50 sin crear recursos" {
        $script:Fake.PortFree = $false
        Invoke-TestOrchestrator -Options (New-TestOptions @{ Validate = $true }) | Should Be 50
        Assert-MockCalled Invoke-DockerCommand -Times 0 -Exactly -Scope It -ParameterFilter { $Arguments -contains "up" }
    }

    It "contexto Docker no local: código 40 sin crear recursos" {
        $script:Fake.Endpoint = "tcp://10.0.0.1:2375"
        Invoke-TestOrchestrator -Options (New-TestOptions @{ Validate = $true }) | Should Be 40
        Assert-MockCalled Invoke-DockerCommand -Times 0 -Exactly -Scope It -ParameterFilter { $Arguments -contains "up" }
    }

    It "flujo -Validate correcto: preflight antes de migrar, limpieza de lo propio y código 0" {
        $options = New-TestOptions @{ Validate = $true; Tests = @("apps/farms/tests/test_a.py"); TestsBound = $true }
        $code = Invoke-TestOrchestrator -Options $options
        $code | Should Be 0
        ($script:Fake.PythonCalls -join ",") | Should Be "migrate,preflight-migrated,pytest"
        $script:Fake.PythonArgs[0] | Should Match "^-m config\.disposable_preflight migrate --settings-module config\.settings_test_disposable"
        Assert-MockCalled Invoke-PythonChild -Times 0 -Exactly -Scope It -ParameterFilter { $Arguments -contains "manage.py" }
        $script:Fake.Containers.Count | Should Be 0
        $script:Fake.Networks.Count | Should Be 0
        Assert-MockCalled Invoke-DockerCommand -Times 0 -Exactly -Scope It -ParameterFilter { $Arguments -contains "-v" }
        $state = Get-TestState -Options $options
        $states = @($state.History | ForEach-Object { $_.State })
        ($states -join ",") | Should Be "NOT_STARTED,ENVIRONMENT_CREATED,PREFLIGHT_PASSED,MIGRATIONS_COMPLETED,TESTS_COMPLETED,CLEANUP_COMPLETED"
        $state.ExitCode | Should Be 0
        $state.InvocationId | Should Match "^[0-9a-f]{32}$"
        Test-IsPathWithin -Child $options.OutputDirectory -Parent $RepoRoot | Should Be $false
        Test-Path -LiteralPath (Join-Path $options.OutputDirectory "compose\invocation.override.yml") | Should Be $true
    }

    It "compose up con override de propiedad, sin .env del repositorio y sin recrear" {
        [void](Invoke-TestOrchestrator -Options (New-TestOptions @{ Validate = $true }))
        Assert-MockCalled Invoke-DockerCommand -Times 1 -Exactly -Scope It -ParameterFilter {
            ($Arguments -contains "up") -and ($Arguments -contains "--no-recreate") -and ($Arguments -contains "--env-file") -and ($Arguments -contains "--project-directory")
        }
    }

    It "fallo parcial (solo red): borra solo la red propia y devuelve 40" {
        $script:Fake.ComposeMode = "networkOnly"
        Invoke-TestOrchestrator -Options (New-TestOptions @{ Validate = $true }) | Should Be 40
        $script:Fake.Networks.Count | Should Be 0
        Assert-MockCalled Invoke-DockerCommand -Times 0 -Exactly -Scope It -ParameterFilter { $Arguments[0] -eq "container" -and $Arguments[1] -eq "rm" }
    }

    It "timeout de compose: limpia lo creado y devuelve 40" {
        $script:Fake.ComposeMode = "timeout"
        Invoke-TestOrchestrator -Options (New-TestOptions @{ Validate = $true }) | Should Be 40
        $script:Fake.Containers.Count | Should Be 0
        $script:Fake.Networks.Count | Should Be 0
    }

    It "propiedad ambigua tras compose: no borra el recurso ajeno y reporta (42)" {
        $script:Fake.ComposeMode = "foreignRace"
        $code = Invoke-TestOrchestrator -Options (New-TestOptions @{ Validate = $true })
        $code | Should Be 42
        $script:Fake.Containers.Count | Should Be 1
        Assert-MockCalled Invoke-DockerCommand -Times 0 -Exactly -Scope It -ParameterFilter { $Arguments[0] -eq "container" -and $Arguments[1] -eq "rm" }
        Assert-MockCalled Invoke-PythonChild -Times 0 -Exactly -Scope It
    }

    It "revalida la propiedad justo antes de borrar" {
        $script:Fake.PythonHook = {
            param($Step)
            if ($Step -eq "pytest") {
                foreach ($c in $script:Fake.Containers.Values) { $c.Labels["agrotech.invocation-id"] = "d" * 32 }
            }
        }
        $code = Invoke-TestOrchestrator -Options (New-TestOptions @{ Validate = $true })
        $code | Should Be 41
        $script:Fake.Containers.Count | Should Be 1
        Assert-MockCalled Invoke-DockerCommand -Times 0 -Exactly -Scope It -ParameterFilter { $Arguments[0] -eq "container" -and $Arguments[1] -eq "rm" }
    }

    It "limpieza fallida tras validación correcta: 41, nunca 0" {
        $script:Fake.RmFails = $true
        $options = New-TestOptions @{ Validate = $true }
        Invoke-TestOrchestrator -Options $options | Should Be 41
        (Get-TestState -Options $options).State | Should Be "CLEANUP_FAILED"
    }

    It "pytest fallido y limpieza fallida: 42" {
        $script:Fake.RmFails = $true
        $script:Fake.PythonCodes["pytest"] = 1
        Invoke-TestOrchestrator -Options (New-TestOptions @{ Validate = $true }) | Should Be 42
    }

    It "pytest fallido con limpieza correcta: 30" {
        $script:Fake.PythonCodes["pytest"] = 1
        Invoke-TestOrchestrator -Options (New-TestOptions @{ Validate = $true }) | Should Be 30
        $script:Fake.Containers.Count | Should Be 0
    }

    It "preflight rechazado: 10, sin pytest y con limpieza" {
        $script:Fake.PythonCodes["migrate"] = 10
        Invoke-TestOrchestrator -Options (New-TestOptions @{ Validate = $true }) | Should Be 10
        ($script:Fake.PythonCalls -join ",") | Should Be "migrate"
        $script:Fake.Containers.Count | Should Be 0
    }

    It "migración fallida: 20, sin pytest" {
        $script:Fake.PythonCodes["migrate"] = 20
        Invoke-TestOrchestrator -Options (New-TestOptions @{ Validate = $true }) | Should Be 20
        ($script:Fake.PythonCalls -join ",") | Should Be "migrate"
    }

    It "identidad del contenedor incorrecta (puerto publicado): 10 sin Python" {
        $script:Fake.Port = "0.0.0.0:3317"
        Invoke-TestOrchestrator -Options (New-TestOptions @{ Validate = $true }) | Should Be 10
        Assert-MockCalled Invoke-PythonChild -Times 0 -Exactly -Scope It
        $script:Fake.Containers.Count | Should Be 0
    }

    It "privilegios globales en el contenedor: 10 sin Python" {
        $script:Fake.Grants = @("GRANT ALL PRIVILEGES ON *.* TO ``u``@``%``", "ROLE:NONE", "ROLES:0")
        Invoke-TestOrchestrator -Options (New-TestOptions @{ Validate = $true }) | Should Be 10
        Assert-MockCalled Invoke-PythonChild -Times 0 -Exactly -Scope It
    }

    It "interrupción (cancelación capturada): 43, estado INTERRUPTED y limpieza de lo propio" {
        $script:Fake.PythonHook = { param($Step) throw (New-Object System.OperationCanceledException) }
        $options = New-TestOptions @{ Validate = $true }
        Invoke-TestOrchestrator -Options $options | Should Be 43
        (Get-TestState -Options $options).State | Should Be "INTERRUPTED"
        $script:Fake.Containers.Count | Should Be 0
    }

    It "modo sin -Validate: solo preflight, código 0 sin anunciar validación" {
        Invoke-TestOrchestrator -Options (New-TestOptions @{}) | Should Be 0
        ($script:Fake.PythonCalls -join ",") | Should Be "preflight"
        $script:Fake.PythonArgs[0] | Should Match "preflight .*--expect-schema empty"
    }

    It "-SqlMigrateOnly: sqlmigrate tras preflight con la configuración aislada" {
        Invoke-TestOrchestrator -Options (New-TestOptions @{ SqlMigrateOnly = $true }) | Should Be 0
        ($script:Fake.PythonCalls -join ",") | Should Be "sqlmigrate"
        $script:Fake.PythonArgs[0] | Should Match "sqlmigrate --settings-module config\.settings_test_disposable_isolated"
    }
}

Describe "CleanupOnly con propiedad demostrable" {
    $script:FakeRoot = New-FakeRoot -Base $TestDrive
    $script:OutBase = $TestDrive
    BeforeEach { $script:Fake = New-FakeDockerState }
    Register-OrchestratorMocks

    function New-StateFile {
        # Desde M3-2.4.3 los estados de prueba son auténticos (DPAPI + HMAC).
        param([string]$RunId, [string]$InvocationId, [string]$ContainerId, [string]$NetworkId)
        return New-TestStateFile -RunId $RunId -InvocationId $InvocationId -ContainerId $ContainerId -NetworkId $NetworkId
    }

    It "elimina recursos con la InvocationId registrada" {
        $runId = "dddddddddddd"; $inv = "1" * 32
        $names = Get-DisposableNames -Id $runId
        $cid = Add-FakeContainer -Name $names.ContainerName -Labels (Get-BaseLabels $runId $inv) -Hostname $names.ContainerHostname
        $nid = Add-FakeNetwork -Name $names.NetworkName -Labels (Get-BaseLabels $runId $inv)
        $state = New-StateFile -RunId $runId -InvocationId $inv -ContainerId $cid -NetworkId $nid
        $code = Invoke-TestOrchestrator -Options @{ Root = $script:FakeRoot; CleanupOnly = $true; RunId = $runId; StateFile = $state }
        $code | Should Be 0
        $script:Fake.Containers.Count | Should Be 0
        $script:Fake.Networks.Count | Should Be 0
    }

    It "no elimina un recurso ajeno con el mismo RunId (50)" {
        $runId = "eeeeeeeeeeee"
        $names = Get-DisposableNames -Id $runId
        $cid = Add-FakeContainer -Name $names.ContainerName -Labels (Get-BaseLabels $runId ("2" * 32)) -Hostname $names.ContainerHostname
        $state = New-StateFile -RunId $runId -InvocationId ("3" * 32) -ContainerId $cid -NetworkId $null
        Invoke-TestOrchestrator -Options @{ Root = $script:FakeRoot; CleanupOnly = $true; RunId = $runId; StateFile = $state } | Should Be 50
        $script:Fake.Containers.ContainsKey($cid) | Should Be $true
        Assert-MockCalled Invoke-DockerCommand -Times 0 -Exactly -Scope It -ParameterFilter { $Arguments -contains "rm" }
    }

    It "no elimina un entorno antiguo sin InvocationId (50)" {
        $runId = "ffffffffffff"
        $names = Get-DisposableNames -Id $runId
        $cid = Add-FakeContainer -Name $names.ContainerName -Labels (Get-BaseLabels $runId $null) -Hostname $names.ContainerHostname
        $state = New-StateFile -RunId $runId -InvocationId ("4" * 32) -ContainerId $null -NetworkId $null
        Invoke-TestOrchestrator -Options @{ Root = $script:FakeRoot; CleanupOnly = $true; RunId = $runId; StateFile = $state } | Should Be 50
        $script:Fake.Containers.ContainsKey($cid) | Should Be $true
    }

    It "rechaza un StateFile sin InvocationId o de otro RunId (50)" {
        $noInv = New-StateFile -RunId "abababababab" -InvocationId $null -ContainerId $null -NetworkId $null
        Invoke-TestOrchestrator -Options @{ Root = $script:FakeRoot; CleanupOnly = $true; RunId = "abababababab"; StateFile = $noInv } | Should Be 50
        $other = New-StateFile -RunId "cdcdcdcdcdcd" -InvocationId ("5" * 32) -ContainerId $null -NetworkId $null
        Invoke-TestOrchestrator -Options @{ Root = $script:FakeRoot; CleanupOnly = $true; RunId = "abababababab"; StateFile = $other } | Should Be 50
        Assert-MockCalled Invoke-DockerCommand -Times 0 -Exactly -Scope It -ParameterFilter { $Arguments -contains "rm" }
    }

    It "rechaza un StateFile dentro del repositorio (3)" {
        $inside = Join-Path $script:FakeRoot "run-state.json"
        Set-Content -LiteralPath $inside -Value "{}" -Encoding UTF8
        Invoke-TestOrchestrator -Options @{ Root = $script:FakeRoot; CleanupOnly = $true; RunId = "abababababab"; StateFile = $inside } | Should Be 3
    }

    It "limpieza fallida en CleanupOnly: 41" {
        $runId = "121212121212"; $inv = "6" * 32
        $names = Get-DisposableNames -Id $runId
        $cid = Add-FakeContainer -Name $names.ContainerName -Labels (Get-BaseLabels $runId $inv) -Hostname $names.ContainerHostname
        $state = New-StateFile -RunId $runId -InvocationId $inv -ContainerId $cid -NetworkId $null
        $script:Fake.RmFails = $true
        Invoke-TestOrchestrator -Options @{ Root = $script:FakeRoot; CleanupOnly = $true; RunId = $runId; StateFile = $state } | Should Be 41
    }
}

# ===========================================================================
# M3-2.4.2: pruebas de las correcciones de la auditoría M3-2.4.1
# ===========================================================================

Describe "AUD-005: contexto Docker solo local" {
    It "acepta los named pipes locales de Docker Desktop" {
        foreach ($endpoint in @("npipe:////./pipe/docker_engine", "npipe:////./pipe/dockerDesktopLinuxEngine", "npipe:////./pipe/dockerDesktopWindowsEngine")) {
            Test-LocalDockerEndpoint -Endpoint $endpoint | Should Be $true
        }
    }
    $rejected = @(
        "npipe:////servidor-remoto/pipe/docker_engine", "npipe:////10.0.0.5/pipe/dockerDesktopLinuxEngine",
        "npipe:////./pipe/otro_pipe", "npipe://./pipe/docker_engine", "npipe:////./pipe/docker_engine/extra",
        "tcp://10.0.0.1:2375", "tcp://127.0.0.1:2375", "ssh://user@host", "unix:///var/run/docker.sock",
        "", "   ", " npipe:////./pipe/docker_engine", "npipe:////./pipe/docker_engine`n", "basura"
    )
    foreach ($endpoint in $rejected) {
        It "rechaza el endpoint '$($endpoint.Trim())'" {
            Test-LocalDockerEndpoint -Endpoint $endpoint | Should Be $false
        }
    }

    Context "en el flujo completo" {
        $script:FakeRoot = New-FakeRoot -Base $TestDrive
        $script:OutBase = $TestDrive
        BeforeEach { $script:Fake = New-FakeDockerState }
        Register-OrchestratorMocks
        foreach ($endpoint in @("npipe:////servidor-remoto/pipe/docker_engine", "tcp://10.0.0.1:2375", "")) {
            It "aborta con 40 sin crear recursos para '$endpoint'" {
                $script:Fake.Endpoint = $endpoint
                Invoke-TestOrchestrator -Options (New-TestOptions @{ Validate = $true }) | Should Be 40
                Assert-MockCalled Invoke-DockerCommand -Times 0 -Exactly -Scope It -ParameterFilter { $Arguments -contains "up" }
                Assert-MockCalled Invoke-PythonChild -Times 0 -Exactly -Scope It
            }
        }
    }
}

Describe "AUD-006: sin credenciales en los logs" {
    It "Protect-SensitiveText redacta VAR=valor, JSON y IDENTIFIED BY" {
        $text = "MARIADB_PASSWORD=$FakeDbSecret`n""MARIADB_ROOT_PASSWORD"": ""$FakeRootSecret""`nAPI_TOKEN=abc123xyz`nIDENTIFIED BY PASSWORD '*ABCDEF'"
        $safe = Protect-SensitiveText -Text $text
        $safe | Should Not Match ([regex]::Escape($FakeDbSecret))
        $safe | Should Not Match ([regex]::Escape($FakeRootSecret))
        $safe | Should Not Match "abc123xyz"
        $safe | Should Not Match "\*ABCDEF"
    }

    It "Get-DockerResource real no registra la salida de inspect ni Config.Env" {
        $logDir = Join-Path $TestDrive ("logs-" + [guid]::NewGuid().ToString("N"))
        New-Item -ItemType Directory -Path $logDir -Force | Out-Null
        $json = ConvertTo-Json -Depth 6 -InputObject @([ordered]@{
                Id = ("a" * 64); Name = "/agrotech-permtest-0123456789ab"
                Config = [ordered]@{ Hostname = "agrotech-permtest-0123456789ab"; Labels = @{ "agrotech.run-id" = "0123456789ab"; "otra.etiqueta" = "x" }
                    Env = @("MARIADB_PASSWORD=$FakeDbSecret", "MARIADB_ROOT_PASSWORD=$FakeRootSecret") }
            })
        # Primero el listado estructurado (AUD-012) y después inspect por ID.
        Mock Invoke-BoundedProcess {
            if ($ArgumentList -contains "ls") { New-FakeResult -Stdout (("a" * 64) + "|agrotech-permtest-0123456789ab") }
            else { New-FakeResult -Stdout $json }
        }
        $ctx = @{ DockerExe = "docker.exe"; DockerContext = "desktop-linux"; DockerEnvironment = @{}; ComposeDirectory = $TestDrive
            Secrets = @(); LogDirectory = $logDir; LogSequence = 0; Timeouts = @{ DockerCommandTimeoutSeconds = 30 }
            TerminationFailures = (New-Object System.Collections.Generic.List[object]); Phase = "cleanup" }
        $resource = Get-DockerResource -Ctx $ctx -Kind container -Ref "agrotech-permtest-0123456789ab"
        $resource.State | Should Be "Present"
        $logs = Get-LogTextUnder -Path $logDir
        $logs | Should Not Match ([regex]::Escape($FakeDbSecret))
        $logs | Should Not Match ([regex]::Escape($FakeRootSecret))
        $logs | Should Not Match "Env"
        $logs | Should Match "agrotech.run-id=0123456789ab"
        $logs | Should Not Match "otra.etiqueta"
    }

    It "tampoco filtra secretos presentes en stderr de errores" {
        $logDir = Join-Path $TestDrive ("logs-" + [guid]::NewGuid().ToString("N"))
        New-Item -ItemType Directory -Path $logDir -Force | Out-Null
        Mock Invoke-BoundedProcess { New-FakeResult -ExitCode 1 -Stderr "daemon error MARIADB_PASSWORD=$FakeDbSecret" }
        $ctx = @{ DockerExe = "docker.exe"; DockerContext = "desktop-linux"; DockerEnvironment = @{}; ComposeDirectory = $TestDrive
            Secrets = @(); LogDirectory = $logDir; LogSequence = 0; Timeouts = @{ DockerCommandTimeoutSeconds = 30 }
            TerminationFailures = (New-Object System.Collections.Generic.List[object]); Phase = "cleanup" }
        (Get-DockerResource -Ctx $ctx -Kind container -Ref "x").State | Should Be "Error"
        Get-LogTextUnder -Path $logDir | Should Not Match ([regex]::Escape($FakeDbSecret))
    }

    Context "flujos completos con secretos ficticios en Config.Env" {
        $script:FakeRoot = New-FakeRoot -Base $TestDrive
        $script:OutBase = $TestDrive
        BeforeEach { $script:Fake = New-FakeDockerState }
        Register-OrchestratorMocks

        It "-CleanupOnly no escribe las contraseñas ficticias en ningún archivo" {
            $runId = "343434343434"; $inv = "7" * 32
            $names = Get-DisposableNames -Id $runId
            $cid = Add-FakeContainer -Name $names.ContainerName -Labels (Get-BaseLabels $runId $inv) -Hostname $names.ContainerHostname
            $state = New-TestStateFile -RunId $runId -InvocationId $inv -ContainerId $cid -NetworkId $null
            Invoke-TestOrchestrator -Options @{ Root = $script:FakeRoot; CleanupOnly = $true; RunId = $runId; StateFile = $state } | Should Be 0
            $all = Get-LogTextUnder -Path (Split-Path -Parent $state)
            $all | Should Not Match ([regex]::Escape($FakeDbSecret))
            $all | Should Not Match ([regex]::Escape($FakeRootSecret))
        }

        It "-CleanupOnly con error de borrado tampoco las escribe" {
            $runId = "565656565656"; $inv = "8" * 32
            $names = Get-DisposableNames -Id $runId
            $cid = Add-FakeContainer -Name $names.ContainerName -Labels (Get-BaseLabels $runId $inv) -Hostname $names.ContainerHostname
            $state = New-TestStateFile -RunId $runId -InvocationId $inv -ContainerId $cid -NetworkId $null
            $script:Fake.RmFails = $true
            Invoke-TestOrchestrator -Options @{ Root = $script:FakeRoot; CleanupOnly = $true; RunId = $runId; StateFile = $state } | Should Be 41
            Get-LogTextUnder -Path (Split-Path -Parent $state) | Should Not Match ([regex]::Escape($FakeDbSecret))
        }

        It "-Validate no escribe las contraseñas ficticias en OutputDirectory" {
            $options = New-TestOptions @{ Validate = $true }
            Invoke-TestOrchestrator -Options $options | Should Be 0
            $all = Get-LogTextUnder -Path $options.OutputDirectory
            $all | Should Not Match ([regex]::Escape($FakeDbSecret))
            $all | Should Not Match ([regex]::Escape($FakeRootSecret))
        }

        It "la verificación de privilegios usa mariadb --raw y no registra su salida" {
            $options = New-TestOptions @{ Validate = $true }
            Invoke-TestOrchestrator -Options $options | Should Be 0
            ($script:Fake.ExecArgs -join "`n") | Should Match "--batch --raw"
            Get-LogTextUnder -Path $options.OutputDirectory | Should Not Match "IDENTIFIED BY PASSWORD"
        }
    }
}

Describe "AUD-007: plazo global de limpieza" {
    $script:FakeRoot = New-FakeRoot -Base $TestDrive
    $script:OutBase = $TestDrive
    BeforeEach {
        $script:Fake = New-FakeDockerState
        $script:Clock = [DateTime]::new(2026, 1, 1, 0, 0, 0, [DateTimeKind]::Utc)
    }
    Register-OrchestratorMocks
    # Cada lectura del reloj de limpieza avanza 25 s: con 30 s de plazo, el
    # plazo se agota antes de terminar.
    Mock Get-CleanupClock { $script:Clock = $script:Clock.AddSeconds(25); $script:Clock }

    It "Get-CleanupStepTimeout nunca supera el tiempo restante" {
        $ctx = @{ Timeouts = @{ DockerCommandTimeoutSeconds = 60 } }
        $deadline = $script:Clock.AddSeconds(40)
        Get-CleanupStepTimeout -Ctx $ctx -Deadline $deadline | Should Be 15
        Get-CleanupStepTimeout -Ctx $ctx -Deadline $deadline | Should Be 0
        Get-CleanupStepTimeout -Ctx $ctx -Deadline $null | Should Be 60
    }

    It "plazo agotado: no borra lo restante, estado CLEANUP_INCOMPLETE y código 41" {
        $options = New-TestOptions @{ Validate = $true; CleanupTimeoutSeconds = 30 }
        Invoke-TestOrchestrator -Options $options | Should Be 41
        (Get-TestState -Options $options).State | Should Be "CLEANUP_INCOMPLETE"
        $script:Fake.Containers.Count | Should Be 1
        Assert-MockCalled Invoke-DockerCommand -Times 0 -Exactly -Scope It -ParameterFilter { $Arguments[1] -eq "rm" }
        Assert-MockCalled Invoke-DockerCommand -Scope It -ParameterFilter { $Arguments[1] -eq "inspect" -and $TimeoutSeconds -gt 0 -and $TimeoutSeconds -le 5 }
    }

    It "con la etapa fallida y el plazo agotado devuelve 42" {
        $script:Fake.PythonCodes["pytest"] = 1
        Invoke-TestOrchestrator -Options (New-TestOptions @{ Validate = $true; CleanupTimeoutSeconds = 30 }) | Should Be 42
    }

    It "-CleanupOnly con plazo agotado: RECOVERY_INCOMPLETE y 41" {
        $runId = "787878787878"; $inv = "9" * 32
        $names = Get-DisposableNames -Id $runId
        $cid = Add-FakeContainer -Name $names.ContainerName -Labels (Get-BaseLabels $runId $inv) -Hostname $names.ContainerHostname
        $nid = Add-FakeNetwork -Name $names.NetworkName -Labels (Get-BaseLabels $runId $inv)
        $state = New-TestStateFile -RunId $runId -InvocationId $inv -ContainerId $cid -NetworkId $nid
        Invoke-TestOrchestrator -Options @{ Root = $script:FakeRoot; CleanupOnly = $true; RunId = $runId; StateFile = $state; CleanupTimeoutSeconds = 30 } | Should Be 41
        $saved = Get-Content -LiteralPath $state -Raw -Encoding UTF8 | ConvertFrom-Json
        $saved.State | Should Be "RECOVERY_INCOMPLETE"
        $script:Fake.Networks.ContainsKey($nid) | Should Be $true
    }
}

Describe "AUD-008: terminación no confirmada" {
    $baseEnv = Get-BaseChildEnvironment

    It "un nieto que sobrevive al hijo se detecta y se termina (terminación confirmada)" {
        $pidFile = Join-Path $TestDrive ("nieto-sale-" + [guid]::NewGuid().ToString("N") + ".pid")
        $command = "`$p = Start-Process -FilePath '$PowerShellExe' -ArgumentList '-NoProfile','-Command','Start-Sleep -Seconds 120' -PassThru -WindowStyle Hidden; Set-Content -LiteralPath '$pidFile' -Value `$p.Id"
        $r = Invoke-BoundedProcess -FilePath $PowerShellExe -ArgumentList @("-NoProfile", "-Command", $command) -TimeoutSeconds 60 -Environment $baseEnv -WorkingDirectory $TestDrive
        $r.TimedOut | Should Be $false
        $grandchild = [int](Get-Content -LiteralPath $pidFile -Raw).Trim()
        @($r.LingeringDescendants) -contains $grandchild | Should Be $true
        $r.TerminationConfirmed | Should Be $true
        Get-Process -Id $grandchild -ErrorAction SilentlyContinue | Should BeNullOrEmpty
    }

    It "si el nieto no puede terminarse, informa los PID supervivientes" {
        Mock Stop-ProcessTree { }
        $pidFile = Join-Path $TestDrive ("nieto-vive-" + [guid]::NewGuid().ToString("N") + ".pid")
        $command = "`$p = Start-Process -FilePath '$PowerShellExe' -ArgumentList '-NoProfile','-Command','Start-Sleep -Seconds 120' -PassThru -WindowStyle Hidden; Set-Content -LiteralPath '$pidFile' -Value `$p.Id"
        $r = Invoke-BoundedProcess -FilePath $PowerShellExe -ArgumentList @("-NoProfile", "-Command", $command) -TimeoutSeconds 60 -Environment $baseEnv -WorkingDirectory $TestDrive -TerminationGraceSeconds 1
        $grandchild = [int](Get-Content -LiteralPath $pidFile -Raw).Trim()
        try {
            $r.TerminationConfirmed | Should Be $false
            @($r.Survivors) -contains $grandchild | Should Be $true
        }
        finally {
            Stop-Process -Id $grandchild -Force -ErrorAction SilentlyContinue
        }
    }

    It "Invoke-DockerCommand registra la terminación no confirmada con fase y PID" {
        Mock Invoke-BoundedProcess { New-FakeResult -Unconfirmed -Survivors @(777, 778) }
        $ctx = @{ DockerExe = "docker.exe"; DockerContext = "desktop-linux"; DockerEnvironment = @{}; ComposeDirectory = $TestDrive
            Secrets = @(); LogDirectory = $null; LogSequence = 0; Timeouts = @{ DockerCommandTimeoutSeconds = 30 }
            TerminationFailures = (New-Object System.Collections.Generic.List[object]); Phase = "stage" }
        [void](Invoke-DockerCommand -Ctx $ctx -Arguments @("compose", "up"))
        $ctx.TerminationFailures.Count | Should Be 1
        $ctx.TerminationFailures[0].Phase | Should Be "stage"
        (@($ctx.TerminationFailures[0].Survivors) -join ",") | Should Be "777,778"
    }

    It "ConvertTo-StageCode no declara éxito con terminación no confirmada" {
        ConvertTo-StageCode -Result (New-FakeResult -ExitCode 0 -Unconfirmed) | Should Be 40
        ConvertTo-StageCode -Result (New-FakeResult -ExitCode 0 -Unconfirmed) -Kind pytest | Should Be 40
        ConvertTo-StageCode -Result (New-FakeResult -ExitCode 0) | Should Be 0
    }

    Context "en el flujo completo" {
        $script:FakeRoot = New-FakeRoot -Base $TestDrive
        $script:OutBase = $TestDrive
        BeforeEach { $script:Fake = New-FakeDockerState }
        Register-OrchestratorMocks

        It "migrate con código 0 pero descendiente vivo: 40, sin pytest" {
            $script:Fake.PythonUnconfirmed["migrate"] = $true
            Invoke-TestOrchestrator -Options (New-TestOptions @{ Validate = $true }) | Should Be 40
            ($script:Fake.PythonCalls -join ",") | Should Be "migrate"
        }

        It "rm con terminación no confirmada: limpieza fallida (41)" {
            $script:Fake.RmUnconfirmed = $true
            $options = New-TestOptions @{ Validate = $true }
            Invoke-TestOrchestrator -Options $options | Should Be 41
            (Get-TestState -Options $options).State | Should Be "CLEANUP_FAILED"
        }

        It "Complete-OrchestratorRun persiste los PID y devuelve 40" {
            $options = New-TestOptions @{}
            New-Item -ItemType Directory -Path $options.OutputDirectory -Force | Out-Null
            $check = Test-OrchestratorOptions -Options $options
            $ctx = New-OrchestratorContext -Normalized $check.Normalized -Root $script:FakeRoot -RunId "0123456789ab" -InvocationId ("a" * 32) -OutputDirectory $options.OutputDirectory
            $ctx.TerminationFailures.Add(@{ Step = "python migrate"; Phase = "stage"; Survivors = @(999); At = "x" })
            Complete-OrchestratorRun -Ctx $ctx -Interrupted $false
            $ctx.ExitCode | Should Be 40
            $saved = Get-TestState -Options $options
            $saved.State | Should Be "NO_RESOURCES_CREATED"
            (@($saved.TerminationFailures)[0].Survivors -join ",") | Should Be "999"
        }
    }
}

Describe "AUD-011: estado persistido coherente" {
    $script:FakeRoot = New-FakeRoot -Base $TestDrive
    $script:OutBase = $TestDrive
    BeforeEach { $script:Fake = New-FakeDockerState }
    Register-OrchestratorMocks

    It "recurso preexistente: NO_RESOURCES_CREATED con ABORTED_PREEXISTING en el historial" {
        $runId = "909090909090"
        $names = Get-DisposableNames -Id $runId
        [void](Add-FakeContainer -Name $names.ContainerName -Labels (Get-BaseLabels $runId $null) -Hostname $names.ContainerHostname)
        $options = New-TestOptions @{ Validate = $true; RunId = $runId }
        Invoke-TestOrchestrator -Options $options | Should Be 50
        $saved = Get-TestState -Options $options
        $saved.State | Should Be "NO_RESOURCES_CREATED"
        (@($saved.History | ForEach-Object { $_.State }) -join ",") | Should Match "ABORTED_PREEXISTING,NO_RESOURCES_CREATED$"
    }

    It "puerto ocupado: NO_RESOURCES_CREATED, nunca CLEANUP_COMPLETED" {
        $script:Fake.PortFree = $false
        $options = New-TestOptions @{ Validate = $true }
        Invoke-TestOrchestrator -Options $options | Should Be 50
        $saved = Get-TestState -Options $options
        $saved.State | Should Be "NO_RESOURCES_CREATED"
        (@($saved.History | ForEach-Object { $_.State }) -contains "CLEANUP_COMPLETED") | Should Be $false
    }

    It "entorno creado y limpiado: CLEANUP_COMPLETED" {
        $options = New-TestOptions @{ Validate = $true }
        Invoke-TestOrchestrator -Options $options | Should Be 0
        (Get-TestState -Options $options).State | Should Be "CLEANUP_COMPLETED"
    }

    Context "persistencia fallida (mock aislado: en Pester 3.4 un Mock dura hasta el final del bloque)" {
        It "un fallo al persistir el estado final no se silencia ni devuelve 0" {
            Mock Save-RunState { if ($Ctx.State -in @("CLEANUP_COMPLETED", "NO_RESOURCES_CREATED")) { throw "disco lleno" } }
            Invoke-TestOrchestrator -Options (New-TestOptions @{ Validate = $true }) | Should Be 40
        }
    }

    It "-CleanupOnly correcto: RECOVERY_COMPLETED conservando los campos previos" {
        $runId = "a1a1a1a1a1a1"; $inv = "b" * 32
        $names = Get-DisposableNames -Id $runId
        $cid = Add-FakeContainer -Name $names.ContainerName -Labels (Get-BaseLabels $runId $inv) -Hostname $names.ContainerHostname
        $state = New-TestStateFile -RunId $runId -InvocationId $inv -ContainerId $cid -NetworkId $null
        Invoke-TestOrchestrator -Options @{ Root = $script:FakeRoot; CleanupOnly = $true; RunId = $runId; StateFile = $state } | Should Be 0
        $saved = Get-Content -LiteralPath $state -Raw -Encoding UTF8 | ConvertFrom-Json
        $saved.State | Should Be "RECOVERY_COMPLETED"
        $saved.RunId | Should Be $runId
        $saved.InvocationId | Should Be $inv
        $saved.Mode | Should Be "Validate"
        $saved.Owned.ContainerId | Should Be $cid
        $saved.Recovery.ExitCode | Should Be 0
        $saved.Recovery.Container | Should Be "Removed"
        (@($saved.History | ForEach-Object { $_.State }) -join ",") | Should Be "NOT_STARTED,RECOVERY_COMPLETED"
    }

    It "-CleanupOnly sobre recurso ajeno: RECOVERY_REFUSED" {
        $runId = "c1c1c1c1c1c1"
        $names = Get-DisposableNames -Id $runId
        $cid = Add-FakeContainer -Name $names.ContainerName -Labels (Get-BaseLabels $runId ("d" * 32)) -Hostname $names.ContainerHostname
        $state = New-TestStateFile -RunId $runId -InvocationId ("e" * 32) -ContainerId $cid -NetworkId $null
        Invoke-TestOrchestrator -Options @{ Root = $script:FakeRoot; CleanupOnly = $true; RunId = $runId; StateFile = $state } | Should Be 50
        (Get-Content -LiteralPath $state -Raw -Encoding UTF8 | ConvertFrom-Json).State | Should Be "RECOVERY_REFUSED"
    }

    It "-CleanupOnly con fallo de borrado: RECOVERY_FAILED" {
        $runId = "f1f1f1f1f1f1"; $inv = "c" * 32
        $names = Get-DisposableNames -Id $runId
        $cid = Add-FakeContainer -Name $names.ContainerName -Labels (Get-BaseLabels $runId $inv) -Hostname $names.ContainerHostname
        $state = New-TestStateFile -RunId $runId -InvocationId $inv -ContainerId $cid -NetworkId $null
        $script:Fake.RmFails = $true
        Invoke-TestOrchestrator -Options @{ Root = $script:FakeRoot; CleanupOnly = $true; RunId = $runId; StateFile = $state } | Should Be 41
        (Get-Content -LiteralPath $state -Raw -Encoding UTF8 | ConvertFrom-Json).State | Should Be "RECOVERY_FAILED"
    }
}

Describe "AUD-019: punto de entrada real (proceso aislado, sin Docker)" {
    # El script real se ejecuta en un powershell.exe hijo con un PATH que solo
    # contiene System32, de modo que Docker no es resoluble: cualquier modo válido
    # termina en 40 ("Docker CLI no encontrado") antes de crear recursos o de
    # lanzar Python. Las opciones transferidas se verifican en run-state.json.
    $entry = Join-Path $RepoRoot "tools\run_permissions_disposable.ps1"
    $entryEnv = Get-BaseChildEnvironment
    $entryEnv["PATH"] = (Join-Path $env:SystemRoot "System32") + ";" + $env:SystemRoot + ";" + (Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0")
    $probe = Invoke-BoundedProcess -FilePath $PowerShellExe -ArgumentList @("-NoProfile", "-Command", "if (Get-Command docker -CommandType Application -ErrorAction SilentlyContinue) { exit 9 } else { exit 0 }") -TimeoutSeconds 60 -Environment $entryEnv -WorkingDirectory $TestDrive
    $dockerHidden = ($probe.ExitCode -eq 0)

    function Invoke-Entry {
        param([string[]]$EntryArgs)
        $all = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $entry) + $EntryArgs
        return Invoke-BoundedProcess -FilePath $PowerShellExe -ArgumentList $all -TimeoutSeconds 120 -Environment $entryEnv -WorkingDirectory $TestDrive
    }

    It "precondición: Docker no es resoluble en el entorno de la prueba" {
        $dockerHidden | Should Be $true
    }

    It "el script solo delega en Invoke-DisposableOrchestrator" {
        $text = Get-Content -LiteralPath $entry -Raw -Encoding UTF8
        $code = ($text -split "#>", 2)[1]
        foreach ($forbidden in @("Invoke-PythonChild", "Invoke-DockerCommand", "Invoke-EnvironmentStages", "manage.py", "Invoke-BoundedProcess")) {
            $code | Should Not Match ([regex]::Escape($forbidden))
        }
        $code | Should Match "Invoke-DisposableOrchestrator -Options"
    }

    $invalid = @(
        @{ Name = "SettingsModule de producción"; Args = @("-Validate", "-SettingsModule", "config.settings") },
        @{ Name = "opción de pytest en -Tests"; Args = @("-Validate", "-Tests", "--ds=config.settings_test") },
        @{ Name = "Validate y SqlMigrateOnly"; Args = @("-Validate", "-SqlMigrateOnly") },
        @{ Name = "CleanupOnly sin StateFile"; Args = @("-CleanupOnly", "-RunId", "0123456789ab") },
        @{ Name = "timeout fuera de rango"; Args = @("-Validate", "-PytestTimeoutSeconds", "5") },
        @{ Name = "HealthTimeoutSeconds fuera de rango"; Args = @("-HealthTimeoutSeconds", "700") },
        @{ Name = "Tests sin Validate"; Args = @("-Tests", "apps/farms/tests") },
        @{ Name = "sin -MariaDbImageDigest (R-ORQ-16)"; Args = @("-Validate"); NoDigest = $true },
        @{ Name = "digest malformado (R-ORQ-16)"; Args = @("-Validate", "-MariaDbImageDigest", "sha256:XYZ"); NoDigest = $true },
        @{ Name = "digest con etiqueta en lugar de sha256"; Args = @("-Validate", "-MariaDbImageDigest", "11.8.8"); NoDigest = $true }
    )
    foreach ($case in $invalid) {
        It "rechaza con 3 y sin crear nada: $($case.Name)" -Skip:(-not $dockerHidden) {
            $out = Join-Path $TestDrive ("entry-" + [guid]::NewGuid().ToString("N"))
            $caseArgs = @($case.Args)
            if (-not $case.ContainsKey("NoDigest") -and $case.Name -notlike "CleanupOnly*") { $caseArgs += @("-MariaDbImageDigest", $TestDigest) }
            $r = Invoke-Entry -EntryArgs ($caseArgs + @("-OutputDirectory", $out))
            if ($case.Name -like "CleanupOnly*") { $r = Invoke-Entry -EntryArgs $case.Args }
            $r.ExitCode | Should Be 3
            Test-Path -LiteralPath $out | Should Be $false
        }
    }

    It "parámetro inexistente: código 1 de PowerShell sin crear nada" -Skip:(-not $dockerHidden) {
        $out = Join-Path $TestDrive ("entry-" + [guid]::NewGuid().ToString("N"))
        (Invoke-Entry -EntryArgs @("-ParametroInexistente", "x", "-OutputDirectory", $out)).ExitCode | Should Be 1
        Test-Path -LiteralPath $out | Should Be $false
    }

    $valid = @(
        @{ Name = "-Validate con -Tests"; Args = @("-Validate", "-Tests", "apps/farms/tests"); Mode = "Validate"; Settings = "config.settings_test_disposable"; Tests = "apps\farms\tests" },
        @{ Name = "-SqlMigrateOnly"; Args = @("-SqlMigrateOnly"); Mode = "SqlMigrateOnly"; Settings = "config.settings_test_disposable_isolated"; Tests = "" },
        @{ Name = "sin switches"; Args = @(); Mode = "EnvironmentOnly"; Settings = "config.settings_test_disposable"; Tests = "" },
        @{ Name = "-Validate aislada"; Args = @("-Validate", "-SettingsModule", "config.settings_test_disposable_isolated", "-Tests", "apps/farms/tests"); Mode = "Validate"; Settings = "config.settings_test_disposable_isolated"; Tests = "apps\farms\tests" }
    )
    foreach ($case in $valid) {
        It "transfiere las opciones y se detiene sin Docker ni Python (40): $($case.Name)" -Skip:(-not $dockerHidden) {
            $out = Join-Path $TestDrive ("entry-" + [guid]::NewGuid().ToString("N"))
            $r = Invoke-Entry -EntryArgs ($case.Args + @("-MariaDbImageDigest", $TestDigest, "-OutputDirectory", $out))
            $r.ExitCode | Should Be 40
            $saved = Get-Content -LiteralPath (Join-Path $out "run-state.json") -Raw -Encoding UTF8 | ConvertFrom-Json
            $saved.Mode | Should Be $case.Mode
            $saved.SettingsModule | Should BeExactly $case.Settings
            (@($saved.Tests) -join ",") | Should Be $case.Tests
            $saved.State | Should Be "NO_RESOURCES_CREATED"
            $saved.StageMessage | Should Match "Docker CLI no encontrado"
            $states = @($saved.History | ForEach-Object { $_.State })
            $states -contains "PREFLIGHT_PASSED" | Should Be $false
            $states -contains "ENVIRONMENT_CREATED" | Should Be $false
            Test-Path -LiteralPath (Join-Path $out "logs\*python*") | Should Be $false
        }
    }
}


# ===========================================================================
# M3-2.4.3: riesgos residuales AUD-009, AUD-012, AUD-013, AUD-014, AUD-016,
# R-ORQ-8 y R-ORQ-16
# ===========================================================================

function New-UnitDockerCtx {
    param([string]$Phase = "stage", [string]$LogDirectory = $null)
    return @{
        DockerExe = "docker.exe"; DockerContext = "desktop-linux"; DockerEnvironment = @{}; ComposeDirectory = $TestDrive
        Secrets = @(); LogDirectory = $LogDirectory; LogSequence = 0; Timeouts = @{ DockerCommandTimeoutSeconds = 30 }
        TerminationFailures = (New-Object System.Collections.Generic.List[object]); Phase = $Phase
    }
}

Describe "AUD-009: creación suspendida y asignación al Job Object" {
    $baseEnv = Get-BaseChildEnvironment

    It "el proceso queda en el Job desde su creación (asignación correcta)" -Skip:(-not (Test-Path -LiteralPath $RepoPython)) {
        Initialize-NativeSupport | Should Be $true
        $job = New-ProcessJob
        try {
            $cmd = (ConvertTo-CommandLineArgument -Value $RepoPython) + " -I -B -c " + (ConvertTo-CommandLineArgument -Value "import time; time.sleep(30)")
            $spawn = [AgrotechDisposableNative]::StartSuspendedInJob($RepoPython, $cmd, $TestDrive, (ConvertTo-EnvironmentBlock -Environment $baseEnv), $job)
            $spawn.Error | Should BeNullOrEmpty
            @(Get-JobProcessIds -Job $job) -contains $spawn.ProcessId | Should Be $true
            [void][AgrotechDisposableNative]::TerminateJob($job)
            @(Wait-JobEmpty -Job $job -TimeoutSeconds 10).Count | Should Be 0
            [AgrotechDisposableNative]::CloseProcess($spawn.ProcessHandle)
            $spawn.StdoutRead.Dispose(); $spawn.StderrRead.Dispose()
        }
        finally { Close-ProcessJob -Job $job }
    }

    Context "asignación fallida (mock aislado)" {
    It "si la asignación al Job falla, el proceso no llega a ejecutar código" -Skip:(-not (Test-Path -LiteralPath $RepoPython)) {
        $marker = Join-Path $TestDrive ("no-ejecutado-" + [guid]::NewGuid().ToString("N") + ".txt")
        $event = New-Object System.Threading.ManualResetEvent($false)
        $script:NotAJob = $event.SafeWaitHandle.DangerousGetHandle()
        try {
            Mock New-ProcessJob { $script:NotAJob }
            Mock Close-ProcessJob { }
            $code = "open(r'$marker', 'w').write('x')"
            $r = Invoke-BoundedProcess -FilePath $RepoPython -ArgumentList @("-I", "-B", "-c", $code) -TimeoutSeconds 30 -Environment $baseEnv -WorkingDirectory $TestDrive
            $r.StartError | Should Match "Job"
            $r.ExitCode | Should BeNullOrEmpty
            Test-Path -LiteralPath $marker | Should Be $false
        }
        finally { $event.Dispose() }
    }
    }

    Context "sin Job (mock aislado)" {
    It "sin Job o sin soporte nativo no se ejecuta nada (falla cerrado)" -Skip:(-not (Test-Path -LiteralPath $RepoPython)) {
        $marker = Join-Path $TestDrive ("no-ejecutado-" + [guid]::NewGuid().ToString("N") + ".txt")
        $code = "open(r'$marker', 'w').write('x')"
        Mock New-ProcessJob { [IntPtr]::Zero }
        (Invoke-BoundedProcess -FilePath $RepoPython -ArgumentList @("-I", "-B", "-c", $code) -TimeoutSeconds 30 -Environment $baseEnv -WorkingDirectory $TestDrive).StartError | Should Match "Job"
        Test-Path -LiteralPath $marker | Should Be $false
    }

    }
    Context "sin soporte nativo (mock aislado)" {
    It "sin soporte nativo tampoco se ejecuta" -Skip:(-not (Test-Path -LiteralPath $RepoPython)) {
        $marker = Join-Path $TestDrive ("no-ejecutado-" + [guid]::NewGuid().ToString("N") + ".txt")
        Mock Initialize-NativeSupport { $false }
        (Invoke-BoundedProcess -FilePath $RepoPython -ArgumentList @("-I", "-B", "-c", "open(r'$marker', 'w').write('x')") -TimeoutSeconds 30 -Environment $baseEnv -WorkingDirectory $TestDrive).StartError | Should Match "nativo"
        Test-Path -LiteralPath $marker | Should Be $false
    }
    }

    It "un proceso que lanza un descendiente y termina: el descendiente queda en el Job y se termina" -Skip:(-not (Test-Path -LiteralPath $RepoPython)) {
        $pidFile = Join-Path $TestDrive ("desc-" + [guid]::NewGuid().ToString("N") + ".pid")
        $code = "import subprocess; p = subprocess.Popen([r'$PowerShellExe', '-NoProfile', '-Command', 'Start-Sleep -Seconds 120']); open(r'$pidFile', 'w').write(str(p.pid))"
        $r = Invoke-BoundedProcess -FilePath $RepoPython -ArgumentList @("-I", "-B", "-c", $code) -TimeoutSeconds 60 -Environment $baseEnv -WorkingDirectory $TestDrive
        $descendant = [int](Get-Content -LiteralPath $pidFile -Raw).Trim()
        $r.ExitCode | Should Be 0
        @($r.LingeringDescendants) -contains $descendant | Should Be $true
        $r.TerminationConfirmed | Should Be $true
        Get-Process -Id $descendant -ErrorAction SilentlyContinue | Should BeNullOrEmpty
    }

    It "terminación por error: código propagado y sin supervivientes" -Skip:(-not (Test-Path -LiteralPath $RepoPython)) {
        $r = Invoke-BoundedProcess -FilePath $RepoPython -ArgumentList @("-I", "-B", "-c", "import sys; sys.stderr.write('fallo'); sys.exit(5)") -TimeoutSeconds 30 -Environment $baseEnv -WorkingDirectory $TestDrive
        $r.ExitCode | Should Be 5
        $r.Stderr | Should Match "fallo"
        $r.TerminationConfirmed | Should Be $true
        @($r.Survivors).Count | Should Be 0
    }
}

Describe "AUD-012: detección estructurada de recursos" {
    $script:LsResult = $null
    $script:InspectResult = $null
    Mock Invoke-DockerCommand {
        if ($Arguments[1] -eq "ls") { return $script:LsResult }
        return $script:InspectResult
    }
    $name = "agrotech-permtest-0123456789ab"
    $id = "b" * 64
    $okInspect = New-FakeResult -Stdout (ConvertTo-Json -Depth 6 -InputObject @([ordered]@{ Id = $id; Name = "/$name"; Config = [ordered]@{ Hostname = $name; Labels = @{} }; Mounts = @() }))

    $cases = @(
        @{ Name = "inexistente (ls 0 sin coincidencias)"; Ls = (New-FakeResult); State = "Absent"; Category = "" },
        @{ Name = "solo coincidencia parcial de nombre"; Ls = (New-FakeResult -Stdout (("c" * 64) + "|$name-net")); State = "Absent"; Category = "" },
        @{ Name = "Docker no disponible"; Ls = (New-FakeResult -ExitCode 1 -Stderr "error during connect: open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified."); State = "Error"; Category = "DaemonUnavailable" },
        @{ Name = "permisos insuficientes"; Ls = (New-FakeResult -ExitCode 1 -Stderr "permission denied while trying to connect"); State = "Error"; Category = "PermissionDenied" },
        @{ Name = "contexto no válido"; Ls = (New-FakeResult -ExitCode 1 -Stderr 'context "remoto": context not found'); State = "Error"; Category = "ContextInvalid" },
        @{ Name = "error desconocido"; Ls = (New-FakeResult -ExitCode 1 -Stderr "algo inesperado"); State = "Error"; Category = "Unknown" },
        @{ Name = "'No such container' en un ls fallido NO es ausencia"; Ls = (New-FakeResult -ExitCode 1 -Stderr "Error: No such container: $name"); State = "Error"; Category = "Unknown" },
        @{ Name = "salida malformada"; Ls = (New-FakeResult -Stdout "esto no es una fila"); State = "Error"; Category = "MalformedOutput" },
        @{ Name = "sin respuesta (timeout)"; Ls = (New-FakeResult -TimedOut); State = "Error"; Category = "NoResponse" },
        @{ Name = "coincidencia ambigua"; Ls = (New-FakeResult -Stdout ((("c" * 64) + "|$name") + "`n" + (("d" * 64) + "|$name"))); State = "Error"; Category = "Ambiguous" }
    )
    foreach ($case in $cases) {
        It "clasifica: $($case.Name)" {
            $script:LsResult = $case.Ls
            $resource = Get-DockerResource -Ctx (New-UnitDockerCtx) -Kind container -Ref $name
            $resource.State | Should Be $case.State
            $resource.Category | Should Be $case.Category
        }
    }

    It "inspección satisfactoria tras un listado inequívoco" {
        $script:LsResult = New-FakeResult -Stdout ("$id|$name")
        $script:InspectResult = $okInspect
        $resource = Get-DockerResource -Ctx (New-UnitDockerCtx) -Kind container -Ref $name
        $resource.State | Should Be "Present"
        $resource.Id | Should Be $id
    }

    It "inspect malformado, fallido o con otro ID es Error, nunca Absent" {
        $script:LsResult = New-FakeResult -Stdout ("$id|$name")
        foreach ($bad in @((New-FakeResult -Stdout "{no json"), (New-FakeResult -ExitCode 1 -Stderr "Error: No such container: $id"),
                (New-FakeResult -Stdout (ConvertTo-Json -Depth 4 -InputObject @([ordered]@{ Id = ("e" * 64); Name = "/$name"; Config = @{} }))))) {
            $script:InspectResult = $bad
            (Get-DockerResource -Ctx (New-UnitDockerCtx) -Kind container -Ref $name).State | Should Be "Error"
        }
    }

    Context "en el flujo completo" {
        $script:FakeRoot = New-FakeRoot -Base $TestDrive
        $script:OutBase = $TestDrive
        BeforeEach { $script:Fake = New-FakeDockerState }
        Register-OrchestratorMocks

        It "un listado fallido con texto 'No such' no se toma como ausencia: 50 sin crear" {
            $script:Fake.LsFailure = "Error: No such container: x"
            Invoke-TestOrchestrator -Options (New-TestOptions @{ Validate = $true }) | Should Be 50
            Assert-MockCalled Invoke-DockerCommand -Times 0 -Exactly -Scope It -ParameterFilter { $Arguments -contains "up" }
        }

        It "-CleanupOnly no declara 'Removed' si la verificación posterior es ambigua" {
            $runId = "a2a2a2a2a2a2"; $inv = "2a" * 16
            $names = Get-DisposableNames -Id $runId
            $cid = Add-FakeContainer -Name $names.ContainerName -Labels (Get-BaseLabels $runId $inv) -Hostname $names.ContainerHostname
            $state = New-TestStateFile -RunId $runId -InvocationId $inv -ContainerId $cid -NetworkId $null
            $script:Fake.RmHook = $true
            Mock Remove-OwnedResource { "Failed" } -ParameterFilter { $Kind -eq "container" }
            Invoke-TestOrchestrator -Options @{ Root = $script:FakeRoot; CleanupOnly = $true; RunId = $runId; StateFile = $state } | Should Be 41
        }
    }
}

Describe "AUD-013: rutas físicas y raíz del repositorio" {
    $spaced = Join-Path $TestDrive "carpeta con espacios"
    New-Item -ItemType Directory -Path $spaced -Force | Out-Null
    $script:FakeRoot = New-FakeRoot -Base $spaced
    $script:OutBase = $spaced
    $rootLink = Join-Path $TestDrive "raiz-enlazada"
    New-Item -ItemType Junction -Path $rootLink -Target $script:FakeRoot | Out-Null
    $insideTarget = Join-Path $script:FakeRoot "destino-interno"
    New-Item -ItemType Directory -Path $insideTarget -Force | Out-Null
    $swapLink = Join-Path $TestDrive "salida-sustituida"
    New-Item -ItemType Junction -Path $swapLink -Target $insideTarget | Out-Null
    BeforeEach { $script:Fake = New-FakeDockerState }
    Register-OrchestratorMocks

    It "acepta una raíz normal con espacios y resuelve su ruta física" {
        Test-RepositoryRootSafe -Root $script:FakeRoot | Should BeNullOrEmpty
        Get-FinalPathOrNull -Path $script:FakeRoot | Should Be (Get-NormalizedFullPath -Path $script:FakeRoot)
        Resolve-AuthorizedTestPath -Root $script:FakeRoot -Item "apps/farms/tests" | Should Be "apps\farms\tests"
    }

    It "flujo completo correcto con una raíz con espacios" {
        Invoke-TestOrchestrator -Options (New-TestOptions @{ Validate = $true }) | Should Be 0
    }

    It "rechaza una raíz que es una junction (3, sin Docker)" {
        Test-RepositoryRootSafe -Root $rootLink | Should Match "symlink o junction"
        $options = New-TestOptions @{ Validate = $true }
        $options.Root = $rootLink
        Invoke-TestOrchestrator -Options $options | Should Be 3
        Assert-MockCalled Invoke-DockerCommand -Times 0 -Exactly -Scope It
    }

    It "rechaza una raíz inexistente" {
        Test-RepositoryRootSafe -Root (Join-Path $TestDrive "no-existe") | Should Match "no existe"
    }

    It "detecta que una ruta externa por junction es físicamente interna" {
        Test-PathPhysicallyWithin -Path (Join-Path $swapLink "x") -Parent $script:FakeRoot | Should Be $true
        { Assert-DirectoryInUse -Path $swapLink -Root $script:FakeRoot } | Should Throw
        { Assert-DirectoryInUse -Path (Join-Path $script:FakeRoot "apps") -Root $script:FakeRoot } | Should Throw
        $outside = Join-Path $TestDrive ("fuera-" + [guid]::NewGuid().ToString("N"))
        New-Item -ItemType Directory -Path $outside -Force | Out-Null
        { Assert-DirectoryInUse -Path $outside -Root $script:FakeRoot } | Should Not Throw
    }

    It "destino que cambia entre validación y uso: 3 y nada escrito en el repositorio" {
        $script:Swapped = $swapLink
        Mock Resolve-SafeOutputDirectory { $script:Swapped }
        Invoke-TestOrchestrator -Options (New-TestOptions @{ Validate = $true }) | Should Be 3
        @(Get-ChildItem -LiteralPath $insideTarget -Force).Count | Should Be 0
        Assert-MockCalled Invoke-DockerCommand -Times 0 -Exactly -Scope It
    }

    It "StateFile en un directorio enlazado se rechaza (3)" {
        $realDir = Join-Path $TestDrive ("estado-real-" + [guid]::NewGuid().ToString("N"))
        New-Item -ItemType Directory -Path $realDir -Force | Out-Null
        Set-Content -LiteralPath (Join-Path $realDir "run-state.json") -Value "{}" -Encoding UTF8
        $linkDir = Join-Path $TestDrive ("estado-enlace-" + [guid]::NewGuid().ToString("N"))
        New-Item -ItemType Junction -Path $linkDir -Target $realDir | Out-Null
        Invoke-TestOrchestrator -Options @{ Root = $script:FakeRoot; CleanupOnly = $true; RunId = "0123456789ab"; StateFile = (Join-Path $linkDir "run-state.json") } | Should Be 3
    }
}

Describe "AUD-014: autenticidad del StateFile y mutex" {
    $script:FakeRoot = New-FakeRoot -Base $TestDrive
    $script:OutBase = $TestDrive
    BeforeEach { $script:Fake = New-FakeDockerState }
    Register-OrchestratorMocks

    function New-RecoveryCase {
        param([string]$RunId, [string]$InvocationId)
        $names = Get-DisposableNames -Id $RunId
        $cid = Add-FakeContainer -Name $names.ContainerName -Labels (Get-BaseLabels $RunId $InvocationId) -Hostname $names.ContainerHostname
        $state = New-TestStateFile -RunId $RunId -InvocationId $InvocationId -ContainerId $cid -NetworkId $null
        return [pscustomobject]@{ State = $state; ContainerId = $cid; Names = $names }
    }

    function Invoke-Cleanup {
        param([string]$RunId, [string]$State)
        return Invoke-TestOrchestrator -Options @{ Root = $script:FakeRoot; CleanupOnly = $true; RunId = $RunId; StateFile = $State }
    }

    It "StateFile válido: integridad correcta y token nunca en claro" {
        $case = New-RecoveryCase -RunId "b1b1b1b1b1b1" -InvocationId ("b1" * 16)
        $saved = Get-Content -LiteralPath $case.State -Raw -Encoding UTF8 | ConvertFrom-Json
        (Test-StateIntegrity -State $saved -Names $case.Names -InvocationId ("b1" * 16)).Ok | Should Be $true
        $secret = Get-TestSecret -InvocationId ("b1" * 16)
        $raw = Get-Content -LiteralPath $case.State -Raw -Encoding UTF8
        $raw | Should Not Match ([regex]::Escape([Convert]::ToBase64String($secret.Token)))
        $raw | Should Not Match (ConvertTo-HexString -Bytes $secret.Token)
        Invoke-Cleanup -RunId "b1b1b1b1b1b1" -State $case.State | Should Be 0
    }

    It "StateFile alterado (ContainerId cambiado): 50 sin borrar" {
        $case = New-RecoveryCase -RunId "b2b2b2b2b2b2" -InvocationId ("b2" * 16)
        $other = Add-FakeContainer -Name "otro-contenedor" -Labels @{} -Hostname "x"
        $json = (Get-Content -LiteralPath $case.State -Raw -Encoding UTF8).Replace($case.ContainerId, $other)
        Set-Content -LiteralPath $case.State -Value $json -Encoding UTF8
        Invoke-Cleanup -RunId "b2b2b2b2b2b2" -State $case.State | Should Be 50
        $script:Fake.Containers.Count | Should Be 2
        Assert-MockCalled Invoke-DockerCommand -Times 0 -Exactly -Scope It -ParameterFilter { $Arguments -contains "rm" }
    }

    It "RunId manipulado en el estado: 50" {
        $case = New-RecoveryCase -RunId "b3b3b3b3b3b3" -InvocationId ("b3" * 16)
        $json = (Get-Content -LiteralPath $case.State -Raw -Encoding UTF8).Replace('"RunId":  "b3b3b3b3b3b3"', '"RunId":  "c3c3c3c3c3c3"')
        Set-Content -LiteralPath $case.State -Value $json -Encoding UTF8
        Invoke-Cleanup -RunId "c3c3c3c3c3c3" -State $case.State | Should Be 50
        $script:Fake.Containers.ContainsKey($case.ContainerId) | Should Be $true
    }

    It "InvocationId manipulado: el token no se descifra (50)" {
        $case = New-RecoveryCase -RunId "b4b4b4b4b4b4" -InvocationId ("b4" * 16)
        $json = (Get-Content -LiteralPath $case.State -Raw -Encoding UTF8).Replace(("b4" * 16), ("c4" * 16))
        Set-Content -LiteralPath $case.State -Value $json -Encoding UTF8
        Invoke-Cleanup -RunId "b4b4b4b4b4b4" -State $case.State | Should Be 50
        $script:Fake.Containers.ContainsKey($case.ContainerId) | Should Be $true
    }

    It "estado auténtico pero recurso sin la prueba de propiedad: 50" {
        $runId = "b5b5b5b5b5b5"; $inv = "b5" * 16
        $names = Get-DisposableNames -Id $runId
        $cid = Add-FakeContainer -Name $names.ContainerName -Labels (Get-BaseLabels $runId $inv -OwnerProof ("0" * 64)) -Hostname $names.ContainerHostname
        $state = New-TestStateFile -RunId $runId -InvocationId $inv -ContainerId $cid -NetworkId $null
        Invoke-Cleanup -RunId $runId -State $state | Should Be 50
        $script:Fake.Containers.ContainsKey($cid) | Should Be $true
    }

    It "estado sin bloque de integridad (versión anterior): 50" {
        $runId = "b6b6b6b6b6b6"; $inv = "b6" * 16
        $names = Get-DisposableNames -Id $runId
        $cid = Add-FakeContainer -Name $names.ContainerName -Labels (Get-BaseLabels $runId $inv) -Hostname $names.ContainerHostname
        $state = New-TestStateFile -RunId $runId -InvocationId $inv -ContainerId $cid -NetworkId $null -NoIntegrity
        Invoke-Cleanup -RunId $runId -State $state | Should Be 50
        $script:Fake.Containers.ContainsKey($cid) | Should Be $true
    }

    It "estado corrupto o incompleto: 3 o 50 sin tocar Docker" {
        $dir = Join-Path $TestDrive ("corrupto-" + [guid]::NewGuid().ToString("N"))
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
        $corrupt = Join-Path $dir "run-state.json"
        Set-Content -LiteralPath $corrupt -Value "{ esto no es json" -Encoding UTF8
        Invoke-Cleanup -RunId "b7b7b7b7b7b7" -State $corrupt | Should Be 3
        $incomplete = Join-Path $dir "incompleto.json"
        Set-Content -LiteralPath $incomplete -Value '{"RunId":"b7b7b7b7b7b7"}' -Encoding UTF8
        Invoke-Cleanup -RunId "b7b7b7b7b7b7" -State $incomplete | Should Be 50
        Assert-MockCalled Invoke-DockerCommand -Times 0 -Exactly -Scope It
    }
}

Describe "AUD-014: mutex Global\ entre procesos" {
    It "usa el espacio de nombres Global\" {
        $script:OrchestratorMutexName | Should Be "Global\AgrotechDisposableOrchestrator"
    }

    It "otro proceso (otra sesión simulada) que tiene el mutex impide entrar" {
        $marker = Join-Path $TestDrive ("mutex-" + [guid]::NewGuid().ToString("N") + ".txt")
        $holder = "`$m = New-Object System.Threading.Mutex(`$false, 'Global\AgrotechDisposableOrchestrator'); [void]`$m.WaitOne(); Set-Content -LiteralPath '$marker' -Value ok; Start-Sleep -Seconds 60"
        $info = New-Object System.Diagnostics.ProcessStartInfo
        $info.FileName = $PowerShellExe
        $info.Arguments = "-NoProfile -Command " + (ConvertTo-CommandLineArgument -Value $holder)
        $info.UseShellExecute = $false
        $info.CreateNoWindow = $true
        $child = [System.Diagnostics.Process]::Start($info)
        try {
            $deadline = (Get-Date).AddSeconds(20)
            while (-not (Test-Path -LiteralPath $marker) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 100 }
            Test-Path -LiteralPath $marker | Should Be $true
            Enter-OrchestratorLock | Should Be $false
        }
        finally {
            if (-not $child.HasExited) { $child.Kill() }
            [void]$child.WaitForExit(10000)
            $child.Dispose()
        }
        Enter-OrchestratorLock | Should Be $true
        Exit-OrchestratorLock
    }
}

Describe "AUD-016: aislamiento y determinismo de Compose" {
    $script:FakeRoot = New-FakeRoot -Base $TestDrive
    $script:OutBase = $TestDrive
    BeforeEach { $script:Fake = New-FakeDockerState }
    Register-OrchestratorMocks

    $names = Get-DisposableNames -Id "0123456789ab"
    $inv = "d1" * 16
    $proof = (Get-TestSecret -InvocationId $inv).Proof
    $ctx = @{ Names = $names; InvocationId = $inv; OwnerProof = $proof; ImageReference = ("mariadb:11.8.8@" + $TestDigest) }
    $override = [pscustomobject]@{ Invocation = $inv; Proof = $proof; Image = ("mariadb:11.8.8@" + $TestDigest) }

    It "acepta la configuración renderizada esperada" {
        $script:Fake.ConfigMutator = $null
        Test-ComposeRenderedConfig -Json (New-FakeRenderedConfig -Ctx $ctx -Override $override) -Ctx $ctx | Should BeNullOrEmpty
    }

    $mutations = [ordered]@{
        "proyecto incorrecto" = { param($c) $c.name = "otro-proyecto" }
        "servicio adicional" = { param($c) $c.services["extra"] = [ordered]@{ image = "x" } }
        "imagen no fijada" = { param($c) $c.services.mariadb.image = "mariadb:11.8.8" }
        "etiqueta owner-proof faltante" = { param($c) $c.services.mariadb.labels.Remove("agrotech.owner-proof") }
        "etiqueta invocation-id distinta" = { param($c) $c.services.mariadb.labels["agrotech.invocation-id"] = ("e" * 32) }
        "puerto en 0.0.0.0" = { param($c) $c.services.mariadb.ports[0].host_ip = "0.0.0.0" }
        "puerto adicional" = { param($c) $c.services.mariadb.ports = @($c.services.mariadb.ports[0], [ordered]@{ host_ip = "127.0.0.1"; target = 22; published = "2222" }) }
        "volumen bind" = { param($c) $c.services.mariadb["volumes"] = @([ordered]@{ type = "bind"; source = "C:\"; target = "/host" }) }
        "env_file" = { param($c) $c.services.mariadb["env_file"] = @("C:\otro\.env") }
        "build" = { param($c) $c.services.mariadb["build"] = [ordered]@{ context = "." } }
        "privilegiado" = { param($c) $c.services.mariadb["privileged"] = $true }
        "red externa" = { param($c) $c.networks.disposable["external"] = $true }
        "red con otro nombre" = { param($c) $c.networks.disposable.name = "bridge" }
        "etiquetas de red faltantes" = { param($c) $c.networks.disposable.labels = @{} }
        "volúmenes de proyecto" = { param($c) $c["volumes"] = [ordered]@{ datos = @{} } }
    }
    foreach ($key in $mutations.Keys) {
        $mutation = $mutations[$key]
        It "rechaza: $key" {
            $script:Fake.ConfigMutator = $mutation
            Test-ComposeRenderedConfig -Json (New-FakeRenderedConfig -Ctx $ctx -Override $override) -Ctx $ctx | Should Not BeNullOrEmpty
        }
    }

    It "rechaza una configuración no interpretable" {
        Test-ComposeRenderedConfig -Json "{ no json" -Ctx $ctx | Should Not BeNullOrEmpty
    }

    It "directorio de Compose: solo override y archivo de entorno vacío" {
        $dir = Join-Path $TestDrive ("compose-" + [guid]::NewGuid().ToString("N"))
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
        $c = @{ ComposeDirectory = $dir; Root = $script:FakeRoot }
        Set-Content -LiteralPath (Join-Path $dir "invocation.override.yml") -Value "x" -Encoding ASCII
        [IO.File]::WriteAllText((Join-Path $dir "empty.env"), "")
        Test-ComposeDirectoryClean -Ctx $c | Should BeNullOrEmpty
        Set-Content -LiteralPath (Join-Path $dir ".env") -Value "DISPOSABLE_DB_NAME=otra" -Encoding ASCII
        Test-ComposeDirectoryClean -Ctx $c | Should Match "no autorizados"
        Remove-Item -LiteralPath (Join-Path $dir ".env")
        Set-Content -LiteralPath (Join-Path $dir "compose.override.yaml") -Value "x" -Encoding ASCII
        Test-ComposeDirectoryClean -Ctx $c | Should Match "no autorizados"
        Remove-Item -LiteralPath (Join-Path $dir "compose.override.yaml")
        Set-Content -LiteralPath (Join-Path $dir "empty.env") -Value "X=1" -Encoding ASCII
        Test-ComposeDirectoryClean -Ctx $c | Should Match "no está vacío"
    }

    It "directorio de trabajo de Compose dentro del repositorio se rechaza" {
        $inside = Join-Path $script:FakeRoot "compose-dentro"
        New-Item -ItemType Directory -Path $inside -Force | Out-Null
        Test-ComposeDirectoryClean -Ctx @{ ComposeDirectory = $inside; Root = $script:FakeRoot } | Should Match "dentro del repositorio"
    }

    It "configuración renderizada no válida en el flujo: 10 y sin 'up'" {
        $script:Fake.ConfigMutator = { param($c) $c.services.mariadb.labels.Remove("agrotech.owner-proof") }
        Invoke-TestOrchestrator -Options (New-TestOptions @{ Validate = $true }) | Should Be 10
        Assert-MockCalled Invoke-DockerCommand -Times 0 -Exactly -Scope It -ParameterFilter { $Arguments -contains "up" }
    }

    It "argumentos deterministas: dos -f, env vacío, proyecto, directorio externo y --pull never" {
        $options = New-TestOptions @{ Validate = $true }
        Invoke-TestOrchestrator -Options $options | Should Be 0
        $up = @($script:Fake.Calls | Where-Object { $_ -match " up " })
        $up.Count | Should Be 1
        ([regex]::Matches($up[0], '(^| )-f ')).Count | Should Be 2
        $up[0] | Should Match ([regex]::Escape("-p " + "agrotech-permtest-"))
        $up[0] | Should Match "--pull never"
        $up[0] | Should Match "--no-recreate"
        $up[0] | Should Match ([regex]::Escape("--project-directory " + (Join-Path $options.OutputDirectory "compose")))
        $up[0] | Should Match ([regex]::Escape("--env-file " + (Join-Path $options.OutputDirectory "compose\empty.env")))
    }

}

Describe "AUD-016: entorno del proceso Docker" {
    $names = Get-DisposableNames -Id "0123456789ab"
    It "Docker recibe un entorno sin COMPOSE_*/DOCKER_* y el directorio de proyecto como cwd" {
        $source = @{ PATH = $env:PATH; SystemRoot = $env:SystemRoot; COMPOSE_FILE = "x.yml"; COMPOSE_PROFILES = "p"; COMPOSE_ENV_FILES = "a.env"; DOCKER_HOST = "tcp://10.0.0.1:2375"; DOCKER_CONTEXT = "remoto"; DOCKER_CONFIG = "C:\x" }
        $script:Captured = $null
        Mock Invoke-BoundedProcess { $script:Captured = @{ Env = $Environment; Cwd = $WorkingDirectory; Args = $ArgumentList }; New-FakeResult }
        $c = New-UnitDockerCtx
        $c.DockerEnvironment = New-DockerChildEnvironment -Names $names -DbPassword "x1x1" -RootPassword "y1y1" -Source $source
        $c.ComposeDirectory = Join-Path $TestDrive "cwd-compose"
        [void](Invoke-DockerCommand -Ctx $c -Arguments @("compose", "version"))
        foreach ($k in $script:Captured.Env.Keys) { $k | Should Not Match '^(COMPOSE_|DOCKER_)' }
        $script:Captured.Cwd | Should Be $c.ComposeDirectory
        ($script:Captured.Args[0..1] -join " ") | Should Be "--context desktop-linux"
    }
}

Describe "R-ORQ-8: volúmenes anónimos" {
    $script:FakeRoot = New-FakeRoot -Base $TestDrive
    $script:OutBase = $TestDrive
    BeforeEach { $script:Fake = New-FakeDockerState }
    Register-OrchestratorMocks

    It "clasifica montajes: anónimos candidatos, nombrados nunca" {
        $anon = New-AnonymousVolumeName
        $resource = [pscustomobject]@{ Mounts = @(
                [pscustomobject]@{ Type = "volume"; Name = $anon; Destination = "/var/lib/mysql" },
                [pscustomobject]@{ Type = "volume"; Name = "datos-compartidos"; Destination = "/datos" },
                [pscustomobject]@{ Type = "bind"; Name = ""; Destination = "/host" }) }
        $result = Get-OwnedVolumeCandidates -Resource $resource
        (@($result.Anonymous) -join ",") | Should Be $anon
        (@($result.Named) -join ",") | Should Be "datos-compartidos"
    }

    It "volumen anónimo propio: se elimina después del contenedor" {
        $anon = New-AnonymousVolumeName
        Add-FakeVolume -Name $anon -Labels @{ "com.docker.volume.anonymous" = "" }
        $script:Fake.NewContainerMounts = @([pscustomobject]@{ Type = "volume"; Name = $anon; Destination = "/var/lib/mysql" })
        Invoke-TestOrchestrator -Options (New-TestOptions @{ Validate = $true }) | Should Be 0
        $script:Fake.Volumes.ContainsKey($anon) | Should Be $false
        $calls = @($script:Fake.Calls)
        $containerRm = [Array]::FindIndex([string[]]$calls, [Predicate[string]] { param($x) $x -like "container rm*" })
        $volumeRm = [Array]::FindIndex([string[]]$calls, [Predicate[string]] { param($x) $x -like "volume rm*" })
        $containerRm | Should BeLessThan $volumeRm
        Assert-MockCalled Invoke-DockerCommand -Times 0 -Exactly -Scope It -ParameterFilter { $Arguments[0] -eq "volume" -and ($Arguments -contains "-f" -or $Arguments -contains "--force" -or $Arguments -contains "prune") }
        Assert-MockCalled Invoke-DockerCommand -Times 0 -Exactly -Scope It -ParameterFilter { $Arguments -contains "-v" }
    }

    It "volumen anónimo del proyecto Compose propio: se elimina" {
        $anon = New-AnonymousVolumeName
        $options = New-TestOptions @{ Validate = $true; RunId = "e1e1e1e1e1e1" }
        Add-FakeVolume -Name $anon -Labels @{ "com.docker.compose.project" = "agrotech-permtest-e1e1e1e1e1e1" }
        $script:Fake.NewContainerMounts = @([pscustomobject]@{ Type = "volume"; Name = $anon; Destination = "/var/lib/mysql" })
        Invoke-TestOrchestrator -Options $options | Should Be 0
        $script:Fake.Volumes.ContainsKey($anon) | Should Be $false
    }

    $kept = @(
        @{ Name = "sin etiquetas"; Labels = @{}; Shared = $false; Code = 41 },
        @{ Name = "de otro proyecto Compose"; Labels = @{ "com.docker.compose.project" = "otro"; "com.docker.volume.anonymous" = "" }; Shared = $false; Code = 41 },
        @{ Name = "compartido con otro contenedor"; Labels = @{ "com.docker.volume.anonymous" = "" }; Shared = $true; Code = 41 }
    )
    foreach ($case in $kept) {
        It "se conserva y se informa: volumen $($case.Name)" {
            $anon = New-AnonymousVolumeName
            Add-FakeVolume -Name $anon -Labels $case.Labels
            if ($case.Shared) { [void](Add-FakeContainer -Name "otro-usuario" -Labels @{} -Hostname "x" -Mounts @([pscustomobject]@{ Type = "volume"; Name = $anon; Destination = "/d" })) }
            $script:Fake.NewContainerMounts = @([pscustomobject]@{ Type = "volume"; Name = $anon; Destination = "/var/lib/mysql" })
            $options = New-TestOptions @{ Validate = $true }
            Invoke-TestOrchestrator -Options $options | Should Be $case.Code
            $script:Fake.Volumes.ContainsKey($anon) | Should Be $true
            (Get-TestState -Options $options).State | Should Be "CLEANUP_FAILED"
            Assert-MockCalled Invoke-DockerCommand -Times 0 -Exactly -Scope It -ParameterFilter { $Arguments[0] -eq "volume" -and $Arguments[1] -eq "rm" }
        }
    }

    It "volumen nombrado montado: nunca se borra, se informa y la ejecución no es un éxito" {
        Add-FakeVolume -Name "datos-compartidos" -Labels @{}
        $script:Fake.NewContainerMounts = @([pscustomobject]@{ Type = "volume"; Name = "datos-compartidos"; Destination = "/datos" })
        Invoke-TestOrchestrator -Options (New-TestOptions @{ Validate = $true }) | Should Be 42
        $script:Fake.Volumes.ContainsKey("datos-compartidos") | Should Be $true
        Assert-MockCalled Invoke-DockerCommand -Times 0 -Exactly -Scope It -ParameterFilter { $Arguments[0] -eq "volume" -and $Arguments[1] -eq "rm" }
    }

    It "propiedad ambigua (volume ls falla): se conserva" {
        $anon = New-AnonymousVolumeName
        Add-FakeVolume -Name $anon -Labels @{ "com.docker.volume.anonymous" = "" }
        $script:Fake.NewContainerMounts = @([pscustomobject]@{ Type = "volume"; Name = $anon; Destination = "/var/lib/mysql" })
        $script:Fake.VolumeLsFailure = "error inesperado"
        Invoke-TestOrchestrator -Options (New-TestOptions @{ Validate = $true }) | Should Be 41
        $script:Fake.Volumes.ContainsKey($anon) | Should Be $true
    }

    It "si el contenedor no se elimina, el volumen tampoco" {
        $anon = New-AnonymousVolumeName
        Add-FakeVolume -Name $anon -Labels @{ "com.docker.volume.anonymous" = "" }
        $script:Fake.NewContainerMounts = @([pscustomobject]@{ Type = "volume"; Name = $anon; Destination = "/var/lib/mysql" })
        $script:Fake.RmFails = $true
        Invoke-TestOrchestrator -Options (New-TestOptions @{ Validate = $true }) | Should Be 41
        $script:Fake.Volumes.ContainsKey($anon) | Should Be $true
    }

    It "fallo parcial: volume rm falla y la limpieza queda fallida" {
        $anon = New-AnonymousVolumeName
        Add-FakeVolume -Name $anon -Labels @{ "com.docker.volume.anonymous" = "" }
        $script:Fake.NewContainerMounts = @([pscustomobject]@{ Type = "volume"; Name = $anon; Destination = "/var/lib/mysql" })
        $script:Fake.VolumeRmFails = $true
        $options = New-TestOptions @{ Validate = $true }
        Invoke-TestOrchestrator -Options $options | Should Be 41
        (Get-TestState -Options $options).State | Should Be "CLEANUP_FAILED"
    }

    It "-CleanupOnly elimina el volumen registrado cuando el contenedor ya no existe" {
        $runId = "e2e2e2e2e2e2"; $inv = "e2" * 16
        $anon = New-AnonymousVolumeName
        Add-FakeVolume -Name $anon -Labels @{ "com.docker.volume.anonymous" = "" }
        $state = New-TestStateFile -RunId $runId -InvocationId $inv -ContainerId $null -NetworkId $null -Volumes @($anon)
        Invoke-TestOrchestrator -Options @{ Root = $script:FakeRoot; CleanupOnly = $true; RunId = $runId; StateFile = $state } | Should Be 0
        $script:Fake.Volumes.ContainsKey($anon) | Should Be $false
        (Get-Content -LiteralPath $state -Raw -Encoding UTF8 | ConvertFrom-Json).Recovery.Volumes | Should Match "=Removed"
    }
}

Describe "R-ORQ-16: imagen fijada por digest y sin descargas" {
    $script:FakeRoot = New-FakeRoot -Base $TestDrive
    $script:OutBase = $TestDrive
    BeforeEach { $script:Fake = New-FakeDockerState }
    Register-OrchestratorMocks

    It "exige un digest con formato válido para crear el entorno" {
        (Test-OrchestratorOptions -Options (New-TestOptions @{ Validate = $true; MariaDbImageDigest = $null })).Ok | Should Be $false
        (Test-OrchestratorOptions -Options (New-TestOptions @{ Validate = $true; MariaDbImageDigest = "sha256:abc" })).Ok | Should Be $false
        (Test-OrchestratorOptions -Options (New-TestOptions @{ Validate = $true; MariaDbImageDigest = ("sha256:" + ("A" * 64)) })).Ok | Should Be $false
        $ok = Test-OrchestratorOptions -Options (New-TestOptions @{ Validate = $true })
        $ok.Ok | Should Be $true
        $ok.Normalized.ImageReference | Should BeExactly ("mariadb:11.8.8@" + $TestDigest)
    }

    It "-CleanupOnly no admite -MariaDbImageDigest" {
        (Test-OrchestratorOptions -Options @{ Root = $script:FakeRoot; CleanupOnly = $true; RunId = "0123456789ab"; StateFile = "C:\x.json"; MariaDbImageDigest = $TestDigest }).Ok | Should Be $false
    }

    It "imagen fijada no disponible en local: 10, sin 'compose config' ni 'up'" {
        $script:Fake.ImageDigests = @(("sha256:" + ("b" * 64)))
        Invoke-TestOrchestrator -Options (New-TestOptions @{ Validate = $true }) | Should Be 10
        Assert-MockCalled Invoke-DockerCommand -Times 0 -Exactly -Scope It -ParameterFilter { $Arguments -contains "up" -or $Arguments -contains "config" }
        Assert-MockCalled Invoke-DockerCommand -Times 0 -Exactly -Scope It -ParameterFilter { $Arguments -contains "pull" }
    }

    It "el override fija la imagen por digest y up usa --pull never" {
        $options = New-TestOptions @{ Validate = $true }
        Invoke-TestOrchestrator -Options $options | Should Be 0
        $override = Get-Content -LiteralPath (Join-Path $options.OutputDirectory "compose\invocation.override.yml") -Raw
        $override | Should Match ([regex]::Escape('image: "mariadb:11.8.8@' + $TestDigest + '"'))
        (@($script:Fake.Calls | Where-Object { $_ -match " up " }) -join "") | Should Match "--pull never"
    }

    It "Compose renderiza otra imagen: 10 y sin 'up'" {
        $script:Fake.ConfigMutator = { param($c) $c.services.mariadb.image = "mariadb:latest" }
        Invoke-TestOrchestrator -Options (New-TestOptions @{ Validate = $true }) | Should Be 10
        Assert-MockCalled Invoke-DockerCommand -Times 0 -Exactly -Scope It -ParameterFilter { $Arguments -contains "up" }
    }
}

# ===========================================================================
# F10.3.4-A: Redis desechable opcional (-Validate -WithRedis). Solo mocks: no
# se ejecuta Docker ni se descarga ninguna imagen.
# ===========================================================================

function New-RedisTestOptions {
    param([hashtable]$Extra = @{})
    $base = @{ Validate = $true; WithRedis = $true; RedisImageDigest = $TestRedisDigest }
    foreach ($key in $Extra.Keys) { $base[$key] = $Extra[$key] }
    return (New-TestOptions $base)
}

function New-RedisStateFile {
    <# run-state.json auténtico v2 (DPAPI + HMAC con los campos de Redis). #>
    param([string]$RunId, [string]$InvocationId, [string]$ContainerId, [string]$NetworkId, [string]$RedisContainerId, [string[]]$Volumes = @())
    $dir = Join-Path $TestDrive ("state-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $dir -Force | Out-Null
    $path = Join-Path $dir "run-state.json"
    $secret = Get-TestSecret -InvocationId $InvocationId
    $history = New-Object System.Collections.Generic.List[object]
    $history.Add(@{ State = "NOT_STARTED"; At = "2026-01-01T00:00:00Z"; Detail = "" })
    $ctx = @{
        StateFile = $path; Names = (Get-DisposableNames -Id $RunId); InvocationId = $InvocationId; Mode = "Validate"
        SettingsModule = "config.settings_test_disposable"; Tests = @(); DockerContext = "desktop-linux"; WithRedis = $true
        Owned = @{ ContainerId = $ContainerId; NetworkId = $NetworkId; Volumes = @($Volumes); RedisContainerId = $RedisContainerId }
        State = "CLEANUP_FAILED"; StageCode = 0; StageMessage = ""; Cleanup = $null; ExitCode = 41
        History = $history; TerminationFailures = (New-Object System.Collections.Generic.List[object])
        OwnerToken = $secret.Token; ProtectedToken = $secret.Protected
    }
    Save-RunState -Ctx $ctx
    return $path
}

Describe "F10.3.4-A: parámetros de Redis" {
    $script:FakeRoot = New-FakeRoot -Base $TestDrive
    $script:OutBase = $TestDrive

    It "-WithRedis exige -Validate" {
        (Test-OrchestratorOptions -Options (New-RedisTestOptions @{ Validate = $false })).Ok | Should Be $false
        (Test-OrchestratorOptions -Options (New-RedisTestOptions @{ Validate = $false; SqlMigrateOnly = $true })).Ok | Should Be $false
    }

    It "-WithRedis exige -RedisImageDigest con formato válido" {
        (Test-OrchestratorOptions -Options (New-RedisTestOptions @{ RedisImageDigest = $null })).Ok | Should Be $false
        (Test-OrchestratorOptions -Options (New-RedisTestOptions @{ RedisImageDigest = "sha256:abc" })).Ok | Should Be $false
        (Test-OrchestratorOptions -Options (New-RedisTestOptions @{ RedisImageDigest = ("sha256:" + ("B" * 64)) })).Ok | Should Be $false
    }

    It "-RedisImageDigest sin -WithRedis se rechaza" {
        (Test-OrchestratorOptions -Options (New-TestOptions @{ Validate = $true; RedisImageDigest = $TestRedisDigest })).Ok | Should Be $false
    }

    It "-CleanupOnly no admite -WithRedis ni -RedisImageDigest" {
        (Test-OrchestratorOptions -Options @{ Root = $script:FakeRoot; CleanupOnly = $true; RunId = "0123456789ab"; StateFile = "C:\x.json"; WithRedis = $true }).Ok | Should Be $false
        (Test-OrchestratorOptions -Options @{ Root = $script:FakeRoot; CleanupOnly = $true; RunId = "0123456789ab"; StateFile = "C:\x.json"; RedisImageDigest = $TestRedisDigest }).Ok | Should Be $false
    }

    It "normaliza la imagen redis:7.4.12 fijada por digest" {
        $ok = Test-OrchestratorOptions -Options (New-RedisTestOptions)
        $ok.Ok | Should Be $true
        $ok.Normalized.WithRedis | Should Be $true
        $ok.Normalized.RedisImageReference | Should BeExactly ("redis:7.4.12@" + $TestRedisDigest)
        (Test-OrchestratorOptions -Options (New-TestOptions @{ Validate = $true })).Normalized.WithRedis | Should Be $false
    }

    It "nombres derivados del RunId para el contenedor Redis" {
        $names = Get-DisposableNames -Id "0123456789ab"
        $names.RedisContainerName | Should BeExactly "agrotech-permtest-0123456789ab-redis"
        $names.RedisContainerHostname | Should BeExactly "agrotech-redis-0123456789ab"
    }
}

Describe "F10.3.4-A: entornos hijo con Redis" {
    $names = Get-DisposableNames -Id "0123456789ab"
    $source = @{ PATH = $env:PATH; SystemRoot = $env:SystemRoot; REDIS_URL = "redis://real:6379/0"; AGROTECH_DISPOSABLE_REDIS = "1" }

    It "con Redis: Python recibe la marca y la contraseña; REDIS_URL sigue vacía" {
        $envMap = New-PythonChildEnvironment -Names $names -SettingsModule "config.settings_test_disposable" -DbPassword "db" -ReportDirectory "C:\out" -Source $source -RedisPassword "redis-secreto"
        $envMap["AGROTECH_DISPOSABLE_REDIS"] | Should BeExactly "1"
        $envMap["AGROTECH_DISPOSABLE_REDIS_PASSWORD"] | Should BeExactly "redis-secreto"
        $envMap["REDIS_URL"] | Should BeExactly ""
    }

    It "sin Redis: ni marca ni contraseña, aunque existan en el entorno heredado" {
        $envMap = New-PythonChildEnvironment -Names $names -SettingsModule "config.settings_test_disposable" -DbPassword "db" -ReportDirectory "C:\out" -Source $source
        $envMap.ContainsKey("AGROTECH_DISPOSABLE_REDIS") | Should Be $false
        $envMap.ContainsKey("AGROTECH_DISPOSABLE_REDIS_PASSWORD") | Should Be $false
        $envMap["REDIS_URL"] | Should BeExactly ""
    }

    It "Docker recibe las variables de Redis solo con contraseña" {
        $with = New-DockerChildEnvironment -Names $names -DbPassword "db" -RootPassword "root" -Source $source -RedisPassword "redis-secreto"
        $with["DISPOSABLE_REDIS_CONTAINER_NAME"] | Should BeExactly $names.RedisContainerName
        $with["DISPOSABLE_REDIS_CONTAINER_HOSTNAME"] | Should BeExactly $names.RedisContainerHostname
        $with["DISPOSABLE_REDIS_PASSWORD"] | Should BeExactly "redis-secreto"
        $without = New-DockerChildEnvironment -Names $names -DbPassword "db" -RootPassword "root" -Source $source
        @($without.Keys | Where-Object { $_ -like "DISPOSABLE_REDIS_*" }).Count | Should Be 0
    }
}

Describe "F10.3.4-A: configuración renderizada con Redis" {
    $names = Get-DisposableNames -Id "0123456789ab"
    $inv = "d2" * 16
    $proof = (Get-TestSecret -InvocationId $inv).Proof
    $ctx = @{ Names = $names; InvocationId = $inv; OwnerProof = $proof; ImageReference = ("mariadb:11.8.8@" + $TestDigest); WithRedis = $true; RedisImageReference = ("redis:7.4.12@" + $TestRedisDigest) }
    $override = [pscustomobject]@{ Invocation = $inv; Proof = $proof; Image = ("mariadb:11.8.8@" + $TestDigest); RedisImage = ("redis:7.4.12@" + $TestRedisDigest) }
    BeforeEach { $script:Fake = New-FakeDockerState }

    It "acepta mariadb + redis esperados" {
        Test-ComposeRenderedConfig -Json (New-FakeRenderedConfig -Ctx $ctx -Override $override) -Ctx $ctx | Should BeNullOrEmpty
    }

    It "sin -WithRedis, un servicio redis se rechaza" {
        $plain = @{ Names = $names; InvocationId = $inv; OwnerProof = $proof; ImageReference = ("mariadb:11.8.8@" + $TestDigest) }
        Test-ComposeRenderedConfig -Json (New-FakeRenderedConfig -Ctx $plain -Override $override) -Ctx $plain | Should Match "servicios inesperados"
    }

    It "con -WithRedis, sin servicio redis se rechaza" {
        $noRedis = [pscustomobject]@{ Invocation = $inv; Proof = $proof; Image = ("mariadb:11.8.8@" + $TestDigest); RedisImage = "" }
        Test-ComposeRenderedConfig -Json (New-FakeRenderedConfig -Ctx $ctx -Override $noRedis) -Ctx $ctx | Should Match "servicios inesperados"
    }

    $mutations = [ordered]@{
        "imagen no fijada" = { param($c) $c.services.redis.image = "redis:7.4.12" }
        "otro container_name" = { param($c) $c.services.redis.container_name = "redis" }
        "otro hostname" = { param($c) $c.services.redis.hostname = "redis" }
        "etiqueta owner-proof faltante" = { param($c) $c.services.redis.labels.Remove("agrotech.owner-proof") }
        "puerto en 0.0.0.0" = { param($c) $c.services.redis.ports[0].host_ip = "0.0.0.0" }
        "puerto publicado distinto" = { param($c) $c.services.redis.ports[0].published = "6379" }
        "puerto adicional" = { param($c) $c.services.redis.ports = @($c.services.redis.ports[0], [ordered]@{ host_ip = "127.0.0.1"; target = 22; published = "2222" }) }
        "volumen" = { param($c) $c.services.redis["volumes"] = @([ordered]@{ type = "volume"; source = "datos"; target = "/data" }) }
        "sin tmpfs" = { param($c) $c.services.redis.Remove("tmpfs") }
        "privilegiado" = { param($c) $c.services.redis["privileged"] = $true }
        "otra red" = { param($c) $c.services.redis.networks = [ordered]@{ bridge = $null } }
    }
    foreach ($key in $mutations.Keys) {
        $mutation = $mutations[$key]
        It "rechaza Redis: $key" {
            $script:Fake.ConfigMutator = $mutation
            Test-ComposeRenderedConfig -Json (New-FakeRenderedConfig -Ctx $ctx -Override $override) -Ctx $ctx | Should Match "^Redis: "
        }
    }
}

Describe "F10.3.4-A: flujo completo con Redis" {
    $script:FakeRoot = New-FakeRoot -Base $TestDrive
    Set-Content -LiteralPath (Join-Path $script:FakeRoot "docker-compose.disposable.redis.yml") -Value "services: {}" -Encoding ASCII
    $script:OutBase = $TestDrive
    BeforeEach { $script:Fake = New-FakeDockerState }
    Register-OrchestratorMocks

    It "crea y elimina MariaDB, Redis y la red; estado v2; tres -f con el override al final" {
        $options = New-RedisTestOptions
        Invoke-TestOrchestrator -Options $options | Should Be 0
        $script:Fake.Containers.Count | Should Be 0
        $script:Fake.Networks.Count | Should Be 0
        $up = @($script:Fake.Calls | Where-Object { $_ -match " up " })
        $up.Count | Should Be 1
        ([regex]::Matches($up[0], '(^| )-f ')).Count | Should Be 3
        $up[0] | Should Match ([regex]::Escape("docker-compose.disposable.redis.yml -f " + (Join-Path $options.OutputDirectory "compose\invocation.override.yml")))
        $up[0] | Should Match "--pull never"
        $override = Get-Content -LiteralPath (Join-Path $options.OutputDirectory "compose\invocation.override.yml") -Raw
        $override | Should Match ([regex]::Escape('image: "redis:7.4.12@' + $TestRedisDigest + '"'))
        $state = Get-TestState -Options $options
        $state.State | Should Be "CLEANUP_COMPLETED"
        $state.Integrity.Version | Should Be "v2"
        $state.RedisContainerName | Should Match "-redis$"
        $state.Owned.RedisContainerId | Should Match '^[0-9a-f]{64}$'
        $state.Cleanup.Redis | Should Be "Removed"
        ($script:Fake.PythonCalls -join ",") | Should Be "migrate,preflight-migrated,pytest"
    }

    It "verifica NOAUTH sin contraseña y escribe la marca del RunId en la base 1" {
        $options = New-RedisTestOptions @{ RunId = "0a0a0a0a0a0a" }
        Invoke-TestOrchestrator -Options $options | Should Be 0
        $execs = $script:Fake.ExecArgs -join "`n"
        $execs | Should Match ([regex]::Escape("unset REDISCLI_AUTH; exec redis-cli -h 127.0.0.1 ping"))
        $execs | Should Match ([regex]::Escape("redis-cli -h 127.0.0.1 -n 1 SET agrotech:disposable:run-id 0a0a0a0a0a0a"))
    }

    It "sin -WithRedis no se toca Redis y el estado sigue en v1" {
        $options = New-TestOptions @{ Validate = $true }
        Invoke-TestOrchestrator -Options $options | Should Be 0
        $up = @($script:Fake.Calls | Where-Object { $_ -match " up " })
        ([regex]::Matches($up[0], '(^| )-f ')).Count | Should Be 2
        ($script:Fake.Calls -join "`n") | Should Not Match "redis"
        $state = Get-TestState -Options $options
        $state.Integrity.Version | Should Be "v1"
        $state.PSObject.Properties["RedisContainerName"] | Should BeNullOrEmpty
    }

    It "contraseña de Redis ausente de logs y del estado" {
        $options = New-RedisTestOptions
        Invoke-TestOrchestrator -Options $options | Should Be 0
        $text = Get-LogTextUnder -Path $options.OutputDirectory
        $text | Should Not Match "FicticiaRedisSecreta-91ab"
        $text | Should Not Match "REDISCLI_AUTH="
    }

    It "imagen Redis no disponible en local: 10, sin 'up' ni descargas" {
        $script:Fake.RedisImageDigests = @(("sha256:" + ("c" * 64)))
        Invoke-TestOrchestrator -Options (New-RedisTestOptions) | Should Be 10
        Assert-MockCalled Invoke-DockerCommand -Times 0 -Exactly -Scope It -ParameterFilter { $Arguments -contains "up" -or $Arguments -contains "pull" }
    }

    It "contenedor Redis preexistente con el nombre del RunId: 50 sin crear nada" {
        $runId = "1b1b1b1b1b1b"
        $names = Get-DisposableNames -Id $runId
        $foreign = Add-FakeContainer -Name $names.RedisContainerName -Labels @{} -Hostname "otro"
        Invoke-TestOrchestrator -Options (New-RedisTestOptions @{ RunId = $runId }) | Should Be 50
        $script:Fake.Containers.ContainsKey($foreign) | Should Be $true
        Assert-MockCalled Invoke-DockerCommand -Times 0 -Exactly -Scope It -ParameterFilter { $Arguments -contains "up" -or $Arguments -contains "rm" }
    }

    It "Redis sin autenticación (responde PONG): 10 sin Python y limpieza completa" {
        $script:Fake.RedisPing = "PONG"
        Invoke-TestOrchestrator -Options (New-RedisTestOptions) | Should Be 10
        Assert-MockCalled Invoke-PythonChild -Times 0 -Exactly -Scope It
        $script:Fake.Containers.Count | Should Be 0
    }

    It "Redis publicado fuera de loopback: 10 sin Python" {
        $script:Fake.RedisPort = "0.0.0.0:16379"
        Invoke-TestOrchestrator -Options (New-RedisTestOptions) | Should Be 10
        Assert-MockCalled Invoke-PythonChild -Times 0 -Exactly -Scope It
        $script:Fake.Containers.Count | Should Be 0
    }

    It "marca de identidad no registrada: 10 sin Python" {
        $script:Fake.RedisSet = "(error) NOAUTH"
        Invoke-TestOrchestrator -Options (New-RedisTestOptions) | Should Be 10
        Assert-MockCalled Invoke-PythonChild -Times 0 -Exactly -Scope It
    }

    It "Redis no llega a crearse en 'up': 40 y se elimina lo propio" {
        $script:Fake.RedisUpMode = "missing"
        Invoke-TestOrchestrator -Options (New-RedisTestOptions) | Should Be 40
        $script:Fake.Containers.Count | Should Be 0
        $script:Fake.Networks.Count | Should Be 0
    }

    It "Redis ajeno con el nombre esperado tras 'up': no se borra y el código final es 42" {
        $script:Fake.RedisUpMode = "foreign"
        $options = New-RedisTestOptions
        Invoke-TestOrchestrator -Options $options | Should Be 42
        $names = Get-DisposableNames -Id (Get-TestState -Options $options).RunId
        @($script:Fake.Containers.Values | Where-Object { $_.Name -eq $names.RedisContainerName }).Count | Should Be 1
        (Get-TestState -Options $options).StageCode | Should Be 50
    }

    It "volumen nombrado en el contenedor Redis: nunca se borra y la ejecución no es un éxito" {
        Add-FakeVolume -Name "redis-compartido" -Labels @{}
        $script:Fake.RedisMounts = @([pscustomobject]@{ Type = "volume"; Name = "redis-compartido"; Destination = "/datos" })
        Invoke-TestOrchestrator -Options (New-RedisTestOptions) | Should Be 42
        $script:Fake.Volumes.ContainsKey("redis-compartido") | Should Be $true
        Assert-MockCalled Invoke-DockerCommand -Times 0 -Exactly -Scope It -ParameterFilter { $Arguments[0] -eq "volume" -and $Arguments[1] -eq "rm" }
    }

    It "falta docker-compose.disposable.redis.yml: 40 sin Docker Compose" {
        $root = New-FakeRoot -Base $TestDrive
        $options = New-RedisTestOptions @{ Root = $root }
        Invoke-TestOrchestrator -Options $options | Should Be 40
        Assert-MockCalled Invoke-DockerCommand -Times 0 -Exactly -Scope It -ParameterFilter { $Arguments[0] -eq "compose" }
    }

    Context "puerto 16379 ocupado (mock aislado)" {
        Mock Test-LoopbackPortFree { $false } -ParameterFilter { $Port -eq 16379 }
        It "50 sin crear recursos ni generar el override" {
            $options = New-RedisTestOptions
            Invoke-TestOrchestrator -Options $options | Should Be 50
            Assert-MockCalled Invoke-DockerCommand -Times 0 -Exactly -Scope It -ParameterFilter { $Arguments -contains "up" }
            (Get-TestState -Options $options).State | Should Be "NO_RESOURCES_CREATED"
        }
    }
}

Describe "F10.3.4-A: integridad v2 y recuperación con Redis" {
    $script:FakeRoot = New-FakeRoot -Base $TestDrive
    $script:OutBase = $TestDrive
    BeforeEach { $script:Fake = New-FakeDockerState }
    Register-OrchestratorMocks

    function New-RedisRecoveryCase {
        param([string]$RunId, [string]$InvocationId, [hashtable]$RedisLabels)
        $names = Get-DisposableNames -Id $RunId
        $labels = Get-BaseLabels $RunId $InvocationId
        if ($null -eq $RedisLabels) { $RedisLabels = $labels }
        $cid = Add-FakeContainer -Name $names.ContainerName -Labels $labels -Hostname $names.ContainerHostname
        $rid = Add-FakeContainer -Name $names.RedisContainerName -Labels $RedisLabels -Hostname $names.RedisContainerHostname
        $nid = Add-FakeNetwork -Name $names.NetworkName -Labels $labels
        $state = New-RedisStateFile -RunId $RunId -InvocationId $InvocationId -ContainerId $cid -NetworkId $nid -RedisContainerId $rid
        return [pscustomobject]@{ Names = $names; ContainerId = $cid; RedisId = $rid; NetworkId = $nid; State = $state }
    }

    It "estado v2 auténtico: Test-StateIntegrity lo acepta y declara Redis" {
        $case = New-RedisRecoveryCase -RunId "2c2c2c2c2c2c" -InvocationId ("4" * 32)
        $state = Get-Content -LiteralPath $case.State -Raw -Encoding UTF8 | ConvertFrom-Json
        $result = Test-StateIntegrity -State $state -Names $case.Names -InvocationId ("4" * 32)
        $result.Ok | Should Be $true
        $result.WithRedis | Should Be $true
    }

    It "-CleanupOnly elimina Redis, MariaDB y la red propios (0)" {
        $case = New-RedisRecoveryCase -RunId "3d3d3d3d3d3d" -InvocationId ("5" * 32)
        Invoke-TestOrchestrator -Options @{ Root = $script:FakeRoot; CleanupOnly = $true; RunId = "3d3d3d3d3d3d"; StateFile = $case.State } | Should Be 0
        $script:Fake.Containers.Count | Should Be 0
        $script:Fake.Networks.Count | Should Be 0
        $recovery = (Get-Content -LiteralPath $case.State -Raw -Encoding UTF8 | ConvertFrom-Json).Recovery
        $recovery.Redis | Should Be "Removed"
        $recovery.State | Should Be "RECOVERY_COMPLETED"
    }

    It "ID de Redis alterado en el estado v2: HMAC inválido, 50 sin borrar nada" {
        $case = New-RedisRecoveryCase -RunId "4e4e4e4e4e4e" -InvocationId ("6" * 32)
        $json = Get-Content -LiteralPath $case.State -Raw -Encoding UTF8 | ConvertFrom-Json
        $json.Owned.RedisContainerId = ("9" * 64)
        Set-Content -LiteralPath $case.State -Value ($json | ConvertTo-Json -Depth 8) -Encoding UTF8
        Invoke-TestOrchestrator -Options @{ Root = $script:FakeRoot; CleanupOnly = $true; RunId = "4e4e4e4e4e4e"; StateFile = $case.State } | Should Be 50
        $script:Fake.Containers.Count | Should Be 2
        Assert-MockCalled Invoke-DockerCommand -Times 0 -Exactly -Scope It -ParameterFilter { $Arguments -contains "rm" }
    }

    It "estado v1 con campos de Redis añadidos: 50 sin borrar nada" {
        $runId = "5f5f5f5f5f5f"; $inv = "7" * 32
        $names = Get-DisposableNames -Id $runId
        $cid = Add-FakeContainer -Name $names.ContainerName -Labels (Get-BaseLabels $runId $inv) -Hostname $names.ContainerHostname
        $state = New-TestStateFile -RunId $runId -InvocationId $inv -ContainerId $cid -NetworkId $null
        $json = Get-Content -LiteralPath $state -Raw -Encoding UTF8 | ConvertFrom-Json
        $json | Add-Member -NotePropertyName "RedisContainerName" -NotePropertyValue $names.RedisContainerName -Force
        Set-Content -LiteralPath $state -Value ($json | ConvertTo-Json -Depth 8) -Encoding UTF8
        Invoke-TestOrchestrator -Options @{ Root = $script:FakeRoot; CleanupOnly = $true; RunId = $runId; StateFile = $state } | Should Be 50
        $script:Fake.Containers.ContainsKey($cid) | Should Be $true
    }

    It "versión v2 rebajada a v1: HMAC inválido, 50" {
        $case = New-RedisRecoveryCase -RunId "6a6a6a6a6a6a" -InvocationId ("8" * 32)
        $json = Get-Content -LiteralPath $case.State -Raw -Encoding UTF8 | ConvertFrom-Json
        $json.Integrity.Version = "v1"
        $json.PSObject.Properties.Remove("RedisContainerName")
        $json.Owned.PSObject.Properties.Remove("RedisContainerId")
        Set-Content -LiteralPath $case.State -Value ($json | ConvertTo-Json -Depth 8) -Encoding UTF8
        Invoke-TestOrchestrator -Options @{ Root = $script:FakeRoot; CleanupOnly = $true; RunId = "6a6a6a6a6a6a"; StateFile = $case.State } | Should Be 50
        $script:Fake.Containers.Count | Should Be 2
    }

    It "Redis ajeno en la recuperación: RECOVERY_REFUSED (50) y no se borra" {
        $case = New-RedisRecoveryCase -RunId "7b7b7b7b7b7b" -InvocationId ("9" * 32) -RedisLabels (Get-BaseLabels "7b7b7b7b7b7b" ("e" * 32))
        Invoke-TestOrchestrator -Options @{ Root = $script:FakeRoot; CleanupOnly = $true; RunId = "7b7b7b7b7b7b"; StateFile = $case.State } | Should Be 50
        $script:Fake.Containers.ContainsKey($case.RedisId) | Should Be $true
        (Get-Content -LiteralPath $case.State -Raw -Encoding UTF8 | ConvertFrom-Json).Recovery.State | Should Be "RECOVERY_REFUSED"
    }
}
