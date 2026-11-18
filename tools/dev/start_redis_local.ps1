<#
.SYNOPSIS
    Inicia el Redis de DESARROLLO local (agrotech-dev-redis) y comprueba su salud.

.DESCRIPTION
    Usa docker-compose.dev.redis.yml (redis:7.4.12 fijado por el digest de
    DEC-12, solo en 127.0.0.1:6379, con contraseña y sin persistencia). La
    contraseña se toma de la variable de usuario AGROTECH_DEV_REDIS_PASSWORD
    (tools/dev/setup_local_secrets.ps1); nunca se imprime. Nunca descarga la
    imagen: si falta, ejecute antes "docker pull" con la referencia de DEC-12.
#>
[CmdletBinding()]
param([int]$TimeoutSeconds = 60)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$password = [Environment]::GetEnvironmentVariable("AGROTECH_DEV_REDIS_PASSWORD", "User")
if ([string]::IsNullOrEmpty($password)) {
    throw "Falta AGROTECH_DEV_REDIS_PASSWORD. Ejecute tools\dev\setup_local_secrets.ps1."
}
$env:AGROTECH_DEV_REDIS_PASSWORD = $password
try {
    & docker compose -f (Join-Path $root "docker-compose.dev.redis.yml") up -d --pull never
    if ($LASTEXITCODE -ne 0) { throw "docker compose up falló (código $LASTEXITCODE)." }
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $health = (& docker inspect agrotech-dev-redis --format "{{.State.Health.Status}}").Trim()
        if ($health -eq "healthy") { Write-Host "Redis local listo en 127.0.0.1:6379 (healthy)."; return }
        Start-Sleep -Seconds 1
    } while ((Get-Date) -lt $deadline)
    throw "Redis local no llegó a healthy en $TimeoutSeconds s (estado: $health)."
}
finally {
    Remove-Item Env:\AGROTECH_DEV_REDIS_PASSWORD -ErrorAction SilentlyContinue
}
