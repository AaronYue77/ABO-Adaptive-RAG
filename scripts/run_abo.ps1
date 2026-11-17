param(
    [ValidateSet('chat','ask','check','cloud-chat','serve','logs')][string]$Command = 'chat',
    [string]$Question = '',
    [switch]$Gpu,
    [switch]$Visual,
    [string]$ModelDir = ''
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Python = $env:ABO_PYTHON
if (-not $Python -and $Gpu) {
    $GpuPython = Join-Path $Root '.venv-gpu/Scripts/python.exe'
    if (Test-Path -LiteralPath $GpuPython) { $Python = $GpuPython }
}
if (-not $Python) { $Python = Join-Path $Root '.venv/Scripts/python.exe' }
if (-not (Test-Path -LiteralPath $Python)) { throw 'Set ABO_PYTHON or create the environment described in README.' }

$LaunchEnvironment = @{
    PYTHONUTF8 = '1'
    PYTHONIOENCODING = 'utf-8'
    PYTHONPATH = $Root
    DOC_VQA_WORKSPACE = (Join-Path $Root 'abo_runtime')
    HF_HOME = (Join-Path $Root '.cache/huggingface')
    HF_HUB_OFFLINE = '1'
    CUDA_CACHE_PATH = (Join-Path $Root '.cache/cuda')
}
$PreviousEnvironment = @{}
foreach ($Key in $LaunchEnvironment.Keys) {
    $PreviousEnvironment[$Key] = [Environment]::GetEnvironmentVariable($Key, 'Process')
    [Environment]::SetEnvironmentVariable($Key, $LaunchEnvironment[$Key], 'Process')
}
$arguments = @('-m','app.abo',$Command)
if ($Question) { $arguments += @('--question',$Question) }
if ($Gpu) { $arguments += '--gpu' }
if ($Visual) { $arguments += '--visual' }
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
