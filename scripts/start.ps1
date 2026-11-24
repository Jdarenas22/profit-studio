param([switch]$Public, [switch]$Stop, [string]$PythonPath)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $projectRoot
if ($Stop) {
    $runtimeRoot = Join-Path $projectRoot '.runtime'
    if (Test-Path -LiteralPath (Join-Path $runtimeRoot 'server.pid')) {
        Set-Content -LiteralPath (Join-Path $runtimeRoot 'stop') -Value 'stop' -Encoding ascii
        Write-Host 'Se solicitó detener la aplicación y su túnel.'
    } else { Write-Host 'No hay un arranque integrado registrado.' }
    exit 0
}
if (-not $PythonPath) { $PythonPath = Join-Path $projectRoot '.venv-secure\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $PythonPath)) { $PythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $PythonPath)) { throw 'Instala requirements/local.txt en el entorno Python del proyecto.' }
$serverArgs = @((Join-Path $PSScriptRoot 'serve.py'))
if ($Public) { $serverArgs += '--public' }
& $PythonPath @serverArgs
exit $LASTEXITCODE
