# Tests scripts/install-piblo.ps1 against a local fixture "release". Run with stock Windows PowerShell:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\test-install-piblo.ps1
$ErrorActionPreference = 'Stop'
$Installer = Join-Path $PSScriptRoot 'install-piblo.ps1'
$Work = Join-Path ([IO.Path]::GetTempPath()) ('piblo-test-' + [guid]::NewGuid().ToString('N'))
$Rel = Join-Path $Work 'release'; $Marker = Join-Path $Work 'ran.txt'
New-Item -ItemType Directory -Path $Rel | Out-Null

# Dummy Setup.exe: writes args to the marker file named by PIBLO_TEST_MARKER.
Add-Type -OutputType ConsoleApplication -OutputAssembly (Join-Path $Rel 'PibloApp-win-Setup.exe') -TypeDefinition @'
public static class P { public static void Main(string[] a) {
  System.IO.File.WriteAllText(System.Environment.GetEnvironmentVariable("PIBLO_TEST_MARKER"), string.Join(" ", a)); } }
'@
$Hash = (Get-FileHash -Algorithm SHA256 (Join-Path $Rel 'PibloApp-win-Setup.exe')).Hash.ToLower()

$Port = 18000 + (Get-Random -Maximum 1000)
$Srv = Start-Process python -ArgumentList "-m http.server $Port --bind 127.0.0.1 --directory `"$Rel`"" -PassThru -WindowStyle Hidden
Start-Sleep -Seconds 2

function Invoke-Case($Base, $Sums) {
    if ($Sums -ne $null) { Set-Content -Path (Join-Path $Rel 'SHA256SUMS.txt') -Value $Sums -Encoding ASCII }
    Remove-Item $Marker -ErrorAction SilentlyContinue
    $env:PIBLO_INSTALL_BASE = $Base; $env:PIBLO_TEST_MARKER = $Marker; $env:LOCALAPPDATA = $Work  # no real Piblo to launch
    $ErrorActionPreference = 'Continue'   # child's stderr must not abort the test
    $out = & powershell -NoProfile -ExecutionPolicy Bypass -File $Installer 2>&1 | Out-String
    $code = $LASTEXITCODE; $ErrorActionPreference = 'Stop'
    [pscustomobject]@{ Exit = $code; Ran = (Test-Path $Marker); Out = $out.Trim() }
}
$fail = 0
function Check($Label, $Ok, $Detail) {
    if ($Ok) { "PASS  $Label" } else { $script:fail++; "FAIL  $Label`n$Detail" }
}
try {
    $Local = "http://127.0.0.1:$Port"
    $r = Invoke-Case $Local "$Hash  PibloApp-win-Setup.exe"
    Check '(a) good checksum -> installer runs silently' ($r.Exit -eq 0 -and $r.Ran -and (Get-Content $Marker) -eq '--silent') $r.Out

    $r = Invoke-Case $Local ('0' * 64 + '  PibloApp-win-Setup.exe')
    Check '(b) bad checksum -> stops, nothing runs' ($r.Exit -ne 0 -and -not $r.Ran -and $r.Out -match 'Checksum mismatch') $r.Out

    $r = Invoke-Case $Local "$Hash  PibloApp-win-Portable.zip"
    Check '(c) missing line -> stops, nothing runs' ($r.Exit -ne 0 -and -not $r.Ran -and $r.Out -match 'no entry') $r.Out

    $r = Invoke-Case 'http://example.com/piblo' $null
    Check '(d) non-https remote base refused' ($r.Exit -ne 0 -and -not $r.Ran -and $r.Out -match 'insecure') $r.Out
} finally {
    Stop-Process -Id $Srv.Id -Force -ErrorAction SilentlyContinue
    Remove-Item $Work -Recurse -Force -ErrorAction SilentlyContinue
}
if ($fail) { "$fail FAILED"; exit 1 } else { 'ALL PASSED' }
