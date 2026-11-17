param(
    [ValidateSet('chat','ask','check','cloud-chat','serve','logs')][string]$Command = 'chat',
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

$RepoPath = Join-Path $Root '.runtime'
$LaunchEnvironment = @{
    PYTHONUTF8 = '1'
    PYTHONIOENCODING = 'utf-8'
    PYTHONPATH = "$Root;$RepoPath"
    DOC_VQA_WORKSPACE = (Join-Path $Root 'abo_runtime')
}
$PreviousEnvironment = @{}
foreach ($Key in $LaunchEnvironment.Keys) {
    $PreviousEnvironment[$Key] = [Environment]::GetEnvironmentVariable($Key, 'Process')
    [Environment]::SetEnvironmentVariable($Key, $LaunchEnvironment[$Key], 'Process')
}
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
    foreach ($Key in $PreviousEnvironment.Keys) {
        if ($null -eq $PreviousEnvironment[$Key]) {
            Remove-Item -LiteralPath "Env:$Key" -ErrorAction SilentlyContinue
        } else {
            [Environment]::SetEnvironmentVariable($Key, $PreviousEnvironment[$Key], 'Process')
        }
    }
}
