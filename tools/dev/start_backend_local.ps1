<#
.SYNOPSIS
    Inicia Django en desarrollo local con config.settings_local (cierre F10.3).

.DESCRIPTION
    Base local agrotech_t (según .env, que Django carga por su cuenta), Redis
    local obligatorio (agrotech-dev-redis en 127.0.0.1:6379) y correo solo en la
    consola de este proceso. Toma INVITATION_CODE_HMAC_KEY y la contraseña de
    Redis de las variables de usuario (tools/dev/setup_local_secrets.ps1) y
    construye REDIS_URL solo para este proceso. No imprime ningún secreto.
#>
[CmdletBinding()]
param([string]$Address = "127.0.0.1:8000")

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$hmac = [Environment]::GetEnvironmentVariable("INVITATION_CODE_HMAC_KEY", "User")
$redisPassword = [Environment]::GetEnvironmentVariable("AGROTECH_DEV_REDIS_PASSWORD", "User")
if ([string]::IsNullOrEmpty($hmac) -or [string]::IsNullOrEmpty($redisPassword)) {
    throw "Faltan secretos locales. Ejecute tools\dev\setup_local_secrets.ps1."
}
$env:INVITATION_CODE_HMAC_KEY = $hmac
$env:REDIS_URL = "redis://:$redisPassword@127.0.0.1:6379/0"
$env:DJANGO_SETTINGS_MODULE = "config.settings_local"
Set-Location $root
& (Join-Path $root ".venv\Scripts\python.exe") manage.py runserver $Address
