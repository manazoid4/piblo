$ErrorActionPreference = "Stop"
$HostRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $HostRoot ".venv\Scripts\python.exe"
$VersionPath = Join-Path $HostRoot "VERSION"
if (-not (Test-Path $VersionPath)) { $VersionPath = Join-Path (Split-Path -Parent $HostRoot) "VERSION" }
$Version = if (Test-Path $VersionPath) { (Get-Content $VersionPath -Raw).Trim() } else { "dev" }

# Core runs on uv, which downloads its own Python: nothing to install by hand.
# uv comes from PATH, else %LocalAppData%\Piblo\tools\uv.exe (fetched once, sha256-checked, no admin).
$UvVersion = "0.11.24"
$UvZipSha256 = "af9573a2e36f7020b18ec5fdde20117aae74bbad3f4acb3dc3fc03319f1aa083"
function Get-Uv {
    $Found = Get-Command uv -ErrorAction SilentlyContinue
    if ($Found) { return $Found.Source }
    $Tools = Join-Path $env:LOCALAPPDATA "Piblo\tools"
    $Exe = Join-Path $Tools "uv.exe"
    if (Test-Path $Exe) { return $Exe }
    New-Item -ItemType Directory -Force -Path $Tools | Out-Null
    $Zip = Join-Path $Tools "uv.zip"
    $Old = $ProgressPreference; $ProgressPreference = "SilentlyContinue"
    try {
        Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/astral-sh/uv/releases/download/$UvVersion/uv-x86_64-pc-windows-msvc.zip" -OutFile $Zip
    } finally { $ProgressPreference = $Old }
    if ((Get-FileHash -Algorithm SHA256 $Zip).Hash.ToLowerInvariant() -ne $UvZipSha256) {
        Remove-Item $Zip -Force
        throw "uv download failed its SHA-256 check; nothing was installed."
    }
    Expand-Archive -Path $Zip -DestinationPath $Tools -Force
    Remove-Item $Zip -Force
    if (-not (Test-Path $Exe)) { throw "uv.exe missing after unpacking." }
    return $Exe
}

if (-not (Test-Path $Python)) {
    $Venv = Join-Path $HostRoot ".venv"
    try {
        $Uv = Get-Uv
        & $Uv venv --seed --python 3.12 $Venv
        if ($LASTEXITCODE -ne 0) { throw "uv venv failed" }
        & $Uv pip install --python $Python -r (Join-Path $HostRoot "requirements.txt")
        if ($LASTEXITCODE -ne 0) { throw "uv pip install failed" }
        $Installed = $true
    } catch {
        Write-Host "uv route failed ($($_.Exception.Message)); trying system Python."
        if (Test-Path $Venv) { Remove-Item $Venv -Recurse -Force }
        $Py = Get-Command py -ErrorAction SilentlyContinue
        $PythonCmd = Get-Command python -ErrorAction SilentlyContinue
        if ($Py) {
            & py -3 -m venv $Venv
        } elseif ($PythonCmd) {
            & python -m venv $Venv
        } else {
            throw "Could not set up Python for Piblo Core (uv failed, and no system Python 3.11+ was found)."
        }
    }
}
if (-not (Get-Variable Installed -ErrorAction SilentlyContinue)) {
    & $Python -m pip install --disable-pip-version-check -r (Join-Path $HostRoot "requirements.txt")
}

$EnvPath = Join-Path $HostRoot ".env"
if (-not (Test-Path $EnvPath)) {
    $Template = Get-Content (Join-Path $HostRoot ".env.example") -Raw
    $Token = & $Python -c "import secrets; print(secrets.token_urlsafe(12))"
    $Template.Replace("change-me-before-first-run", $Token) | Set-Content $EnvPath -NoNewline
}

$Address = Get-NetIPAddress -AddressFamily IPv4 | Where-Object {
    $_.InterfaceAlias -eq "Wi-Fi" -and $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*"
} | Select-Object -First 1 -ExpandProperty IPAddress
if (-not $Address) {
    $Address = Get-NetIPAddress -AddressFamily IPv4 | Where-Object {
        $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" -and $_.PrefixOrigin -ne "WellKnown"
    } | Select-Object -First 1 -ExpandProperty IPAddress
}
$ConfiguredToken = (Select-String -Path $EnvPath -Pattern '^MAZ_TOKEN=(.+)$').Matches.Groups[1].Value
$Sha = [Security.Cryptography.SHA256]::Create()
try {
    $Digest = $Sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($ConfiguredToken))
    $TokenId = -join ($Digest[0..5] | ForEach-Object { $_.ToString("x2") })
} finally {
    $Sha.Dispose()
}
Write-Host "MAZ Core v$Version base setup is ready."
Write-Host "Cardputer pairing if needed:"
Write-Host "  Address: ${Address}:8787"
Write-Host "  Token ID: $TokenId (secret remains in .env and on the paired Cardputer)"
Write-Host "Run install-core.ps1 for the full setup. With v0.6 open over USB, pair.ps1 updates Core details without asking for Wi-Fi credentials."
