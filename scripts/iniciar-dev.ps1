param(
    [string]$WorkspaceRoot = (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)),
    # ExpoGo: el pago vuelve a exp://IP:8081/--/pago. Compilada: vuelve al esquema real de app.json (sportmatchapp://pago).
    [ValidateSet('ExpoGo', 'Compilada')][string]$ModoApp = 'ExpoGo',
    [int]$PuertoWeb = 3001,
    [string]$LanIp = ''
)

$ErrorActionPreference = 'Stop'
$workspace = (Resolve-Path -LiteralPath $WorkspaceRoot).Path
$logs = Join-Path $workspace 'docs'
$gateway = Split-Path -Parent $PSScriptRoot
New-Item -ItemType Directory -Path $logs -Force | Out-Null
$node = (Get-Command node.exe).Source

# La IP de la LAN se calcula UNA sola vez y de ella salen todas las direcciones que usa el teléfono.
# (-LanIp la fuerza si el equipo tiene varias redes.)
$lanAddress = $LanIp
if (-not $lanAddress) {
    $lanAddress = Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -and $_.IPv4Address } |
        Select-Object -First 1 -ExpandProperty IPv4Address | Select-Object -First 1 -ExpandProperty IPAddress
}
if (-not $lanAddress) { throw 'No se detectó la IP de la LAN. Ejecuta: .\iniciar-dev.ps1 -LanIp 192.168.x.x' }

$env:WEBPAY_PUBLIC_URL = "http://${lanAddress}:8000"
# Pago iniciado desde la app -> vuelve a la app. Pago iniciado desde la web (client=web) -> WEBPAY_WEB_RETURN_URL.
$env:WEBPAY_FRONTEND_RETURN_URL = if ($ModoApp -eq 'Compilada') { 'sportmatchapp://pago' } else { "exp://${lanAddress}:8081/--/pago" }
$env:WEBPAY_WEB_RETURN_URL = "http://localhost:${PuertoWeb}/pago/resultado"
$env:ALLOWED_HOSTS = "localhost,127.0.0.1,gateway,testserver,$lanAddress"
$env:GATEWAY_BIND = '0.0.0.0'
$env:CORS_ALLOWED_ORIGINS = (@(
    "http://localhost:8081", "http://127.0.0.1:8081", "http://${lanAddress}:8081",
    "http://localhost:${PuertoWeb}", "http://127.0.0.1:${PuertoWeb}", "http://${lanAddress}:${PuertoWeb}"
) -join ',')

Push-Location $gateway
try {
    # Docker escribe progreso en stderr incluso cuando termina correctamente.
    # Windows PowerShell no debe convertir ese progreso en una excepción.
    $ErrorActionPreference = 'Continue'
    docker compose -f compose.yaml -f compose.windows.yaml up --build -d
    $composeExitCode = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    if ($composeExitCode -ne 0) { throw 'No se pudo iniciar el backend.' }
} finally { Pop-Location }

function Iniciar-Frontend($Carpeta, $Ejecutable, $Argumentos, $Puerto, $Nombre) {
    if (Get-NetTCPConnection -LocalPort $Puerto -State Listen -ErrorAction SilentlyContinue) {
        Write-Host "$Nombre ya tiene un proceso en el puerto $Puerto."
        return
    }
    $directorio = Join-Path $workspace $Carpeta
    $entrada = Join-Path $directorio $Ejecutable
    Start-Process -FilePath $node -ArgumentList (@('"' + $entrada + '"') + $Argumentos) `
        -WorkingDirectory $directorio -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logs "$Nombre-dev.log") `
        -RedirectStandardError (Join-Path $logs "$Nombre-dev-error.log") | Out-Null
}

Iniciar-Frontend 'Frontend-SportMatch-APP' 'node_modules/expo/bin/cli' @('start', '--go', '--offline', '--port', '8081') 8081 'expo'
Iniciar-Frontend 'sportmatch-web' 'node_modules/next/dist/bin/next' @('dev', '--port', "$PuertoWeb") $PuertoWeb 'web'
Write-Host 'App: http://localhost:8081'
Write-Host "Web de clubes: http://localhost:$PuertoWeb"
Write-Host 'API: http://localhost:8000/docs'
Write-Host ''
Write-Host "IP de la LAN:                $lanAddress"
Write-Host "EXPO_PUBLIC_API_URL (app):   http://${lanAddress}:8000   <- ponla en el .env de la app si no la detecta sola"
Write-Host "Retorno del pago en la app:  $env:WEBPAY_FRONTEND_RETURN_URL"
Write-Host "Retorno del pago en la web:  $env:WEBPAY_WEB_RETURN_URL"
