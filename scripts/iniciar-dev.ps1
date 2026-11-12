param([string]$WorkspaceRoot = (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)))

$ErrorActionPreference = 'Stop'
$workspace = (Resolve-Path -LiteralPath $WorkspaceRoot).Path
$logs = Join-Path $workspace 'docs'
$gateway = Split-Path -Parent $PSScriptRoot
New-Item -ItemType Directory -Path $logs -Force | Out-Null
$node = (Get-Command node.exe).Source

# Los enlaces de Webpay los abre el teléfono. Usa la IP actual del equipo,
# también para regresar a Expo Go, en lugar de reutilizar una IP antigua del .env.
$lanAddress = Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -and $_.IPv4Address } |
    Select-Object -First 1 -ExpandProperty IPv4Address | Select-Object -First 1 -ExpandProperty IPAddress
if ($lanAddress) {
    $env:WEBPAY_PUBLIC_URL = "http://${lanAddress}:8000"
    $env:WEBPAY_FRONTEND_RETURN_URL = "exp://${lanAddress}:8081/--/pago"
}

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
Iniciar-Frontend 'sportmatch-web' 'node_modules/next/dist/bin/next' @('dev', '--port', '3001') 3001 'web'
Write-Host 'App: http://localhost:8081'
Write-Host 'Web de clubes: http://localhost:3001'
Write-Host 'API: http://localhost:8000/docs'
