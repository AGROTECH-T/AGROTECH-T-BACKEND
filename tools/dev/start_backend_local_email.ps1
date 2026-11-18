<#
.SYNOPSIS
    Inicia Django en desarrollo local con config.settings_local_email (correo real por Brevo).

.DESCRIPTION
    Igual que start_backend_local.ps1 (base local segun .env, Redis local
    obligatorio en 127.0.0.1:6379), pero con config.settings_local_email: el
    correo SALE del equipo por Brevo (API si BREVO_API_KEY esta en .env; si no,
    SMTP heredado). Usar solo con direcciones propias.
    Toma INVITATION_CODE_HMAC_KEY y la contrasena de Redis de las variables de
    usuario (tools/dev/setup_local_secrets.ps1) y construye REDIS_URL solo para
    este proceso. No imprime ningun secreto ni modifica variables persistentes.
    Para convivir con el backend de consola, use otra direccion, por ejemplo
    -Address 127.0.0.1:8001.
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
$env:DJANGO_SETTINGS_MODULE = "config.settings_local_email"
Set-Location $root
& (Join-Path $root ".venv\Scripts\python.exe") manage.py runserver $Address
