<#
.SYNOPSIS
    Prepara los secretos de DESARROLLO local sin mostrarlos (cierre F10.3).

.DESCRIPTION
    Crea, solo si no existen, estas variables de entorno de USUARIO de Windows:
      - INVITATION_CODE_HMAC_KEY: 48 bytes aleatorios (base64url, 64 caracteres),
        distinta de SECRET_KEY. Firma los códigos de invitación.
      - AGROTECH_DEV_REDIS_PASSWORD: 32 bytes aleatorios en hexadecimal.
    REDIS_URL no se guarda como variable de usuario (es un nombre genérico que
    otros proyectos podrían leer): tools/dev/start_backend_local.ps1 la
    construye solo dentro del proceso del backend.
    No lee ni modifica .env, no sobrescribe valores existentes y nunca imprime
    los valores: solo informa si cada variable ya existía o se creó.
    python-dotenv no sobrescribe variables existentes, así que estas tienen
    prioridad sobre .env.

    Rotar la clave HMAC invalida los códigos pendientes. Para rotarla, borre la
    variable a mano y vuelva a ejecutar el script.
#>
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function New-RandomBytes([int]$Count) {
    $bytes = New-Object byte[] $Count
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return , $bytes
}

function Set-UserVariableIfMissing([string]$Name, [scriptblock]$Factory) {
    $current = [Environment]::GetEnvironmentVariable($Name, "User")
    if (-not [string]::IsNullOrEmpty($current)) {
        Write-Host "$Name ya existe: no se modifica."
        return $false
    }
    [Environment]::SetEnvironmentVariable($Name, (& $Factory), "User")
    Write-Host "$Name creada (valor no mostrado)."
    return $true
}

[void](Set-UserVariableIfMissing "INVITATION_CODE_HMAC_KEY" {
    $b64 = [Convert]::ToBase64String((New-RandomBytes 48))
    $b64.TrimEnd("=").Replace("+", "-").Replace("/", "_")
})
[void](Set-UserVariableIfMissing "AGROTECH_DEV_REDIS_PASSWORD" {
    -join ((New-RandomBytes 32) | ForEach-Object { $_.ToString("x2") })
})
Write-Host "Listo. Los scripts de tools\dev leen las variables de usuario directamente."
