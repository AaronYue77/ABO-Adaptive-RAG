$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Check = "import ensurepip, sqlite3, struct, sys, venv; assert sys.version_info >= (3, 11) and sys.prefix == sys.base_prefix and struct.calcsize('P') == 8; sqlite3.connect(':memory:').execute('CREATE VIRTUAL TABLE probe USING fts5(text)')"

function Test-WebPython($File, $Prefix = @()) {
    if (-not $File -or $File -like '*\Microsoft\WindowsApps\*' -or -not (Test-Path -LiteralPath $File -PathType Leaf)) { return $false }
    $ErrorActionPreference = 'Continue'
    & $File @Prefix -I -c $Check 2>$null
    return $LASTEXITCODE -eq 0
}

function Get-ArchiveHash($File) {
    $stream = [IO.File]::OpenRead($File)
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-', '') }
    finally { $sha.Dispose(); $stream.Dispose() }
}

function Get-WebPython {
    foreach ($environment in '.venv-web', '.venv') {
        $cfg = Join-Path $Root "$environment/pyvenv.cfg"
        if (-not (Test-Path -LiteralPath $cfg -PathType Leaf)) { continue }
        $homeLine = Get-Content -LiteralPath $cfg -Encoding UTF8 | Where-Object { $_ -match '^home\s*=' } | Select-Object -First 1
        if ($homeLine) {
            $python = Join-Path ($homeLine -replace '^home\s*=\s*', '') 'python.exe'
            if (Test-WebPython $python) { return @{File=$python; Prefix=@()} }
        }
    }
    foreach ($candidate in Get-Command python.exe, python3.exe, py.exe -CommandType Application -All -ErrorAction SilentlyContinue) {
        $prefix = @()
        if ($candidate.Name -eq 'py.exe') { $prefix = @('-3') }
        if (Test-WebPython $candidate.Source $prefix) { return @{File=$candidate.Source; Prefix=$prefix} }
    }

    if (-not [Environment]::Is64BitOperatingSystem) { throw 'ABO requires 64-bit Windows.' }
    $platform, $archiveName, $checksum = (Get-Content -LiteralPath (Join-Path $PSScriptRoot 'web_python_assets.txt') |
        Where-Object { $_.StartsWith('windows-x64 ') }) -split ' '
    $target = Join-Path $Root ".cache/abo-web-python/$($archiveName.Replace('.tar.gz', ''))"
    $python = Join-Path $target 'python/python.exe'
    $ready = Join-Path $target '.ready'
    if ((Test-Path -LiteralPath $ready) -and (Test-WebPython $python)) { return @{File=$python; Prefix=@()} }

    $tar = Get-Command tar.exe -CommandType Application -ErrorAction SilentlyContinue
    if (-not $tar) { throw 'Windows tar.exe is required to prepare the project Python.' }
    $release = 'https://github.com/astral-sh/python-build-standalone/releases/download/20261110'
    $archive = Join-Path $target $archiveName
    $pending = "$archive.$PID.part"
    New-Item -ItemType Directory -Path $target -Force | Out-Null
    if (-not (Test-Path -LiteralPath $archive) -or (Get-ArchiveHash $archive) -ne $checksum) {
        Write-Host 'Downloading Python 3.11 for this project...'
        try {
            $url = "$release/$($archiveName.Replace('+', '%2B'))"
            $curl = Get-Command curl.exe -CommandType Application -ErrorAction SilentlyContinue
            if ($curl) {
                & $curl.Source --fail --location --silent --show-error --retry 2 --connect-timeout 20 --max-time 150 --output $pending $url
                if ($LASTEXITCODE -ne 0) { throw 'Could not download Python. Check access to GitHub and retry.' }
            } else {
                $ProgressPreference = 'SilentlyContinue'
                [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
                Invoke-WebRequest -Uri $url -UseBasicParsing -OutFile $pending -TimeoutSec 150
            }
            if ((Get-ArchiveHash $pending) -ne $checksum) { throw 'Downloaded Python checksum did not match.' }
            Move-Item -LiteralPath $pending -Destination $archive -Force
        } finally {
            if (Test-Path -LiteralPath $pending) { Remove-Item -LiteralPath $pending }
        }
    }
    & $tar.Source -xzf $archive -C $target
    if ($LASTEXITCODE -ne 0 -or -not (Test-WebPython $python)) { throw 'The project Python could not start.' }
    New-Item -ItemType File -Path $ready -Force | Out-Null
    return @{File=$python; Prefix=@()}
}

try {
    $selected = Get-WebPython
    $prefix = $selected.Prefix
    & $selected.File @prefix (Join-Path $PSScriptRoot 'start_web.py') @args
    exit $LASTEXITCODE
} catch {
    Write-Error -ErrorAction Continue "ABO could not start: $($_.Exception.Message)"
    exit 1
}
