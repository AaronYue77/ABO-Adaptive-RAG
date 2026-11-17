param(
    [ValidateSet('chat','ask','check','cloud-chat','serve')][string]$Command = 'chat',
    [string]$Question = '',
    [switch]$Gpu,
    [string]$ModelDir = ''
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Python = $env:ABO_PYTHON
$LocalPython = Join-Path $Root '.local/python.txt'
if (-not $Python -and (Test-Path -LiteralPath $LocalPython)) {
    $Python = (Get-Content -LiteralPath $LocalPython -Raw).Trim()
}
if (-not $Python) { $Python = Join-Path $Root '.venv/Scripts/python.exe' }
if (-not (Test-Path -LiteralPath $Python)) { throw 'Set ABO_PYTHON or create the environment described in README.' }

$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$RepoPath = Join-Path $Root '.runtime'
$env:PYTHONPATH = "$Root;$RepoPath"
$env:DOC_VQA_WORKSPACE = Join-Path $Root 'abo_runtime'
$arguments = @('-m','app.abo',$Command)
if ($Question) { $arguments += @('--question',$Question) }
if ($Gpu) { $arguments += '--gpu' }
if ($ModelDir) { $arguments += @('--model-dir',$ModelDir) }
Push-Location $Root
try {
    & $Python @arguments
    if ($LASTEXITCODE -ne 0) { throw "ABO command failed ($LASTEXITCODE)." }
} finally {
    Pop-Location
}
