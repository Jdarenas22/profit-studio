# Compila Tailwind (v3.4, versión fija) a static/css/app.css.
# Uso: .\scripts\build-css.ps1 [--watch]
# Requiere Node/npx solo en el equipo local; Railway usa el CSS ya compilado que está en git.
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Rest)
$ErrorActionPreference = 'Stop'
$TailwindVersion = '3.4.19'
Set-Location (Join-Path $PSScriptRoot '..')

$cliArgs = @('-c', 'tailwind.config.js', '-i', 'frontend/tailwind.css', '-o', 'static/css/app.css', '--minify')
if ($Rest -contains '--watch') { $cliArgs += '--watch' }

& npx.cmd --yes "tailwindcss@$TailwindVersion" @cliArgs
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
