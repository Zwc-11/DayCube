# The built installer, checked as a user would get it, with no one watching (CI, windows-latest):
#  1. install silently (per-user, no admin);
#  2. seed a sample vault with the installed Python, and record meaning search's memory;
#  3. start a receiver the way a repository checkout does, so the app must take it over;
#  4. run DayCube.exe --self-test: the API answers, the window loads the viewer and its data, the
#     injected token is accepted, the receiver answers on 127.0.0.1:7700;
#  5. screenshot the screen while the window is open;
#  6. after it exits, no Python process from the install is left running (job objects).
# Writes everything to $Out. Exits non-zero on any failure.
param(
  [Parameter(Mandatory = $true)][string]$Installer,
  [Parameter(Mandatory = $true)][string]$Out
)
$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force -Path $Out | Out-Null

function Fail($why) {
  Write-Host "::error::$why"
  exit 1
}

# 1. Install.
$size = (Get-Item $Installer).Length / 1MB
Write-Host ("Installer: {0:N1} MB" -f $size)
"installer_mb=$([math]::Round($size, 1))" | Out-File -Append "$Out/sizes.txt"
$p = Start-Process -FilePath $Installer -ArgumentList '/S' -Wait -PassThru
if ($p.ExitCode -ne 0) { Fail "the installer exited with $($p.ExitCode)" }
$exe = @("$env:LOCALAPPDATA\DayCube\DayCube.exe", "$env:LOCALAPPDATA\Programs\DayCube\DayCube.exe") |
  Where-Object { Test-Path $_ } | ForEach-Object { Get-Item $_ } | Select-Object -First 1
if (-not $exe) {
  $exe = Get-ChildItem -Path "$env:LOCALAPPDATA" -Filter 'DayCube.exe' -Recurse -Depth 3 -ErrorAction SilentlyContinue | Select-Object -First 1
}
if (-not $exe) { Fail 'DayCube.exe is not under LOCALAPPDATA after installing' }
$app = $exe.DirectoryName
Write-Host "Installed at $app"
$installed = (Get-ChildItem -Recurse -File $app | Measure-Object -Sum Length).Sum / 1MB
"installed_mb=$([math]::Round($installed, 1))" | Out-File -Append "$Out/sizes.txt"
$python = Join-Path $app 'runtime\core\python.exe'
if (-not (Test-Path $python)) { Fail "no bundled Python at $python" }

# 2. A sample vault, made with the installed Python only.
$vault = Join-Path $env:RUNNER_TEMP 'daycube-vault'
$env:DAYCUBE_VAULT_ROOT = $vault
$env:PYTHONNOUSERSITE = '1'
& $python -c "import sys; from ingest.cli import main; sys.argv=['daycube','seed']; main()"
if ($LASTEXITCODE -ne 0) { Fail 'seeding the vault with the installed Python failed' }
& $python -c "import sys; from ingest.cli import main; sys.argv=['daycube','index','--date','2026-10-20']; main()"
if ($LASTEXITCODE -ne 0) { Fail 'indexing the vault with the installed Python failed' }
# Meaning search's memory with the installed Python: the model loaded, then let go (recorded only).
$probe = @'
import time, psutil
from ingest.index.embed import FastEmbedder
mb = lambda: round(psutil.Process().memory_info().rss / 2**20)
embedder = FastEmbedder(idle_s=2)
start = mb(); embedder.query('demo order'); loaded = mb(); time.sleep(4)
print(f'embed_rss_mb=start {start}, loaded {loaded}, let go {mb()}')
'@
& $python -c $probe | Tee-Object -Append -FilePath "$Out/sizes.txt"

# 3. A receiver started outside the app, as capture.cmd does.
$foreign = Start-Process -FilePath $python -ArgumentList '-m', 'capture', 'run', '--receive', '--no-screen', '--no-inputs' `
  -WorkingDirectory (Join-Path $app 'src') -WindowStyle Hidden -PassThru `
  -RedirectStandardError "$Out/foreign-receiver.err" -RedirectStandardOutput "$Out/foreign-receiver.out"
$deadline = (Get-Date).AddSeconds(90)
do {
  Start-Sleep -Seconds 2
  try { $up = (Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 http://127.0.0.1:7700/status).StatusCode -eq 200 } catch { $up = $false }
} until ($up -or (Get-Date) -gt $deadline)
if (-not $up) { Fail 'the outside receiver did not start (see foreign-receiver.err)' }
Write-Host "Outside receiver running (pid $($foreign.Id))"
Remove-Item Env:DAYCUBE_VAULT_ROOT

# 4. The self-test.
$report = Join-Path $Out 'selftest.json'
$run = Start-Process -FilePath $exe.FullName -ArgumentList '--self-test', '--vault', "`"$vault`"", '--report', "`"$report`"", '--hold', '25' -PassThru
$deadline = (Get-Date).AddMinutes(8)
while (-not (Test-Path $report) -and -not $run.HasExited -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 2 }

# 5. The screen while the window is still open (the app holds it for 25 s after the report).
if (-not $run.HasExited) {
  Start-Sleep -Seconds 6
  Add-Type -AssemblyName System.Windows.Forms, System.Drawing
  $bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
  $bitmap = New-Object System.Drawing.Bitmap $bounds.Width, $bounds.Height
  [System.Drawing.Graphics]::FromImage($bitmap).CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
  $bitmap.Save((Join-Path $Out 'window.png'), [System.Drawing.Imaging.ImageFormat]::Png)
}
if (-not $run.WaitForExit(120000)) { $run.Kill(); Fail 'DayCube did not exit after the self-test' }
$code = $run.ExitCode
Copy-Item -Recurse -Force (Join-Path $vault 'logs') (Join-Path $Out 'vault-logs') -ErrorAction SilentlyContinue
if (-not (Test-Path $report)) { Fail "no self-test report (exit code $code)" }
Get-Content $report
$result = Get-Content $report -Raw | ConvertFrom-Json
if (-not $result.pass -or $code -ne 0) { Fail "self-test failed (exit $code): $($result.problems -join '; ')" }
if (-not (Select-String -Quiet -Path (Join-Path $vault 'logs\desktop.log') -Pattern 'started outside the app')) {
  Fail 'the app did not take over the receiver started outside it'
}
if (-not $foreign.HasExited) { Fail 'the outside receiver is still running: the takeover did not stop it' }

# 6. Nothing from the install left running.
Start-Sleep -Seconds 3
$left = Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($app, [StringComparison]::OrdinalIgnoreCase) }
if ($left) {
  $left | Select-Object ProcessId, CommandLine | Format-List | Out-String | Write-Host
  Fail 'processes from the install outlived the app'
}
Write-Host 'Smoke test passed.'
