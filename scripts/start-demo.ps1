param([switch]$Real, [switch]$FreeTierConfirmed)
$ErrorActionPreference = 'Stop'
$demoRoot = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $demoRoot
$python = Join-Path $demoRoot '.venv-secure\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { $python = Join-Path $demoRoot '.venv\Scripts\python.exe' }
if ($Real -and -not $FreeTierConfirmed) { throw 'Confirma primero el nivel gratuito del proyecto de Google: -FreeTierConfirmed.' }
$env:DEMO_AI_MODE = if ($Real) { 'demo' } else { 'simulated' }
$env:DEMO_FREE_TIER_CONFIRMED = if ($Real -and $FreeTierConfirmed) { 'true' } else { 'false' }
& $python manage.py migrate --settings=config.settings.demo --noinput
if ($LASTEXITCODE -ne 0) { throw 'No se pudo preparar la base académica.' }
& $python manage.py seed_academic_demo --settings=config.settings.demo
if ($LASTEXITCODE -ne 0) { throw 'No se pudieron preparar las cuentas ficticias.' }
& $python manage.py load_usda_catalog --apply --activate-demo --settings=config.settings.demo
if ($LASTEXITCODE -ne 0) { throw 'No se pudo preparar el catálogo USDA de la muestra.' }
Write-Host 'Demo: http://127.0.0.1:8010/demo/ · Accesos: .demo/access.local.txt · Ctrl+C para detener.'
& $python manage.py runserver 127.0.0.1:8010 --noreload --settings=config.settings.demo
