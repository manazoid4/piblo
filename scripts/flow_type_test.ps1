<#
Piblo Flow PC-side proof: does Core really type into a focused Windows window?
No device needed. Run it in the INTERACTIVE session of the machine that runs Core
(MAZ-LAPTOP), e.g. via a scheduled task set to "Run only when user is logged on".

  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\flow_type_test.ps1 [-Core http://127.0.0.1:8787] [-Phrase "..."] [-Out result.txt]

What it does (existing routes only: POST /dictate?target=pc-paste):
  1. Synthesises Phrase to a 16 kHz mono WAV with Windows SAPI (stands in for the Cardputer mic).
  2. Opens a fresh Notepad, gives it real focus with a mouse click in its text area.
  3. POSTs the WAV exactly as the Cardputer does and times the round trip.
  4. Reads Notepad's text back through UI Automation and prints PASS/FAIL.
PASS = Core reported "pasted" AND >=80% of the phrase words appear in Notepad. Exit 0 PASS, 1 FAIL, 2 setup error.
The Core token is read from %LOCALAPPDATA%\Piblo\Core\.env and is never printed.
#>
param(
    [string]$Core = "http://127.0.0.1:8787",
    [string]$Phrase = "um so this is a test of piblo flow typed into notepad",
    [string]$Out = ""
)
$ErrorActionPreference = "Stop"
$log = New-Object System.Collections.Generic.List[string]
function Say($m) { $line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss.fff"), $m; $log.Add($line); Write-Host $line }
function Finish($code, $verdict) {
    Say $verdict
    if ($Out) { $log | Set-Content -Path $Out -Encoding UTF8 }
    exit $code
}

Add-Type -AssemblyName System.Speech, UIAutomationClient, UIAutomationTypes
Add-Type @"
using System; using System.Runtime.InteropServices;
public static class FlowWin {
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint dx, uint dy, uint d, UIntPtr e);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern IntPtr GetAncestor(IntPtr h, uint f);
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
}
"@
[void][FlowWin]::SetProcessDPIAware()

# --- token (never printed)
$envFile = Join-Path $env:LOCALAPPDATA "Piblo\Core\.env"
if (-not (Test-Path $envFile)) { Finish 2 "SETUP ERROR: $envFile not found (is Core installed on this PC?)" }
$tok = ((Get-Content $envFile | Where-Object { $_ -like "MAZ_TOKEN=*" } | Select-Object -First 1) -replace "^MAZ_TOKEN=", "").Trim().Trim('"')
if (-not $tok) { Finish 2 "SETUP ERROR: no MAZ_TOKEN in $envFile" }
try { $null = Invoke-RestMethod -Uri "$Core/health" -Headers @{ Authorization = "Bearer $tok" } -TimeoutSec 5 }
catch { Finish 2 "SETUP ERROR: Core not reachable at $Core ($($_.Exception.Message))" }

# --- 1. WAV
$wav = Join-Path $env:TEMP "piblo_flow_type_test.wav"
$syn = New-Object System.Speech.Synthesis.SpeechSynthesizer
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
$syn.SetOutputToWaveFile($wav, $fmt); $syn.Speak($Phrase); $syn.Dispose()
Say "wav ready: $((Get-Item $wav).Length) bytes"

# --- 2. Notepad with real focus
$np = Start-Process notepad -PassThru
$deadline = (Get-Date).AddSeconds(15); $win = $null
while ((Get-Date) -lt $deadline -and -not $win) {
    Start-Sleep -Milliseconds 300
    # Windows 11 Notepad hands off to another process: find the window by title/class, not by PID.
    $win = [System.Windows.Automation.AutomationElement]::RootElement.FindFirst(
        [System.Windows.Automation.TreeScope]::Children,
        (New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ClassNameProperty, "Notepad")))
}
if (-not $win) { Finish 2 "SETUP ERROR: Notepad window did not appear" }
$edit = $win.FindFirst([System.Windows.Automation.TreeScope]::Descendants,
    (New-Object System.Windows.Automation.OrCondition(
        (New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::Document)),
        (New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::Edit)))))
if (-not $edit) { Finish 2 "SETUP ERROR: Notepad text area not found" }
function ReadText { try { $edit.GetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern).DocumentRange.GetText(-1) } catch { try { $edit.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern).Current.Value } catch { "" } } }
$r = $edit.Current.BoundingRectangle
[void][FlowWin]::SetCursorPos([int]($r.X + 40), [int]($r.Y + 20))
[FlowWin]::mouse_event(2, 0, 0, 0, [UIntPtr]::Zero); [FlowWin]::mouse_event(4, 0, 0, 0, [UIntPtr]::Zero)
Start-Sleep -Milliseconds 600
$hwnd = [IntPtr]$win.Current.NativeWindowHandle
$fg = [FlowWin]::GetAncestor([FlowWin]::GetForegroundWindow(), 2)
if ($fg -ne $hwnd) { Finish 2 "SETUP ERROR: could not give Notepad focus (run in the interactive desktop session, not a background service)" }
$before = ReadText
Say "notepad focused; text before: $($before.Length) chars"

# --- 3. the Cardputer's request
$sw = [Diagnostics.Stopwatch]::StartNew()
try {
    $resp = Invoke-RestMethod -Method Post -Uri "$Core/dictate?target=pc-paste" -InFile $wav -ContentType "audio/wav" `
        -Headers @{ Authorization = "Bearer $tok" } -TimeoutSec 60
} catch { Finish 1 "FAIL: Core request failed after $($sw.ElapsedMilliseconds) ms: $($_.Exception.Message)" }
$roundTrip = $sw.ElapsedMilliseconds
Say "core replied in $roundTrip ms: pc=$($resp.pc) intent=$($resp.intent) text='$($resp.text)'"

# --- 4. read back
Start-Sleep -Milliseconds 500
$after = ReadText
$added = if ($after.StartsWith($before)) { $after.Substring($before.Length) } else { $after }
Say "notepad now holds: '$($added.Trim())'"
$want = ($Phrase.ToLower() -replace "[^a-z ]", "" -split "\s+") | Where-Object { $_ -and $_ -ne "um" }
$have = ($added.ToLower() -replace "[^a-z ]", "" -split "\s+")
$hit = ($want | Where-Object { $have -contains $_ }).Count
$pct = [math]::Round(100 * $hit / [math]::Max(1, $want.Count))
Say "word match $hit/$($want.Count) ($pct%)"

try { Stop-Process -Id $np.Id -Force -ErrorAction SilentlyContinue; Get-Process notepad -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -eq $hwnd } | Stop-Process -Force } catch {}
if ($resp.pc -eq "pasted" -and $pct -ge 80) { Finish 0 "PASS: typed into focused Notepad, round trip $roundTrip ms" }
Finish 1 "FAIL: pc=$($resp.pc), match $pct% (clipboard fallback or wrong text; see lines above)"
