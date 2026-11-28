# Piblo one-line installer.   irm https://<piblo site>/install.ps1 | iex
# Downloads Piblo-win-Setup.exe from the GitHub release, checks its SHA-256
# against SHA256SUMS.txt, then runs it silently. No admin needed. Re-run to update.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # makes Invoke-WebRequest much faster on 5.1
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch { }

$Base = 'https://github.com/manazoid4/piblo/releases/download/app-latest'
if ($env:PIBLO_INSTALL_BASE) { $Base = $env:PIBLO_INSTALL_BASE }   # tests only
$Base = $Base.TrimEnd('/')
if ($Base -notmatch '^https://' -and $Base -notmatch '^http://(127\.0\.0\.1|localhost)(:\d+)?(/|$)') {
    throw "Refusing to download over an insecure address: $Base"
}

$Name = 'Piblo-win-Setup.exe'
$Tmp = Join-Path ([IO.Path]::GetTempPath()) ('piblo-install-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $Tmp | Out-Null
try {
    Write-Host 'Piblo: downloading...'
    Invoke-WebRequest -UseBasicParsing -Uri "$Base/SHA256SUMS.txt" -OutFile (Join-Path $Tmp 'SHA256SUMS.txt')
    Invoke-WebRequest -UseBasicParsing -Uri "$Base/$Name" -OutFile (Join-Path $Tmp $Name)
    $File = Join-Path $Tmp $Name

    # Lines look like "<sha256>  <filename>" (a leading * before the name is allowed).
    $Line = Get-Content (Join-Path $Tmp 'SHA256SUMS.txt') |
        Where-Object { $_ -match "^\s*([0-9a-fA-F]{64})\s+\*?$([regex]::Escape($Name))\s*$" } |
        Select-Object -First 1
    if (-not $Line) { throw "SHA256SUMS.txt has no entry for $Name. Nothing was run." }
    $Want = ($Line.Trim() -split '\s+')[0].ToLowerInvariant()
    $Got = (Get-FileHash -Algorithm SHA256 -Path $File).Hash.ToLowerInvariant()
    if ($Got -ne $Want) {
        Remove-Item $File -Force
        throw "Checksum mismatch for $Name (expected $Want, got $Got). The file was deleted and nothing was run."
    }
    Write-Host 'Piblo: checksum OK, installing...'

    # Velopack Setup.exe: --silent hides dialogs; it also skips the first launch, so we start Piblo below.
    $Run = Start-Process -FilePath $File -ArgumentList '--silent' -Wait -PassThru
    if ($Run.ExitCode -ne 0) { throw "Setup exited with code $($Run.ExitCode)." }

    $Update = Join-Path $env:LOCALAPPDATA 'Piblo\Update.exe'
    if (Test-Path $Update) {
        Write-Host 'Piblo: installed. Starting it now.'
        Start-Process -FilePath $Update -ArgumentList 'start'
    } else {
        Write-Host 'Piblo: setup finished, but Piblo was not found in %LocalAppData%\Piblo.'
    }
} finally {
    Remove-Item $Tmp -Recurse -Force -ErrorAction SilentlyContinue
}
