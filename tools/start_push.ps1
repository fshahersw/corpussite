# Starts the data upload hidden and detached, so it survives the session that launched it.
# Progress: _transfer_scratch\push.log    Stop: Stop-Process -Id (Get-Content _transfer_scratch\push.pid)    Resume: run this again.
param([string[]]$PushArguments = @())
$root = Split-Path -Parent $PSScriptRoot
$python = 'C:\Users\firas\AppData\Local\Programs\Python\Python311\python.exe'
$scratch = Join-Path $root '_transfer_scratch'
New-Item -ItemType Directory -Force $scratch | Out-Null
$existing = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like '*push_data.py*push*' }
if ($existing) { "already running: pid $($existing.ProcessId)"; exit 0 }
$arguments = @('-u', (Join-Path $root 'tools\push_data.py'), 'push') + $PushArguments
$process = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $root -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $scratch 'push.stdout.log') -RedirectStandardError (Join-Path $scratch 'push.stderr.log')
Set-Content -Path (Join-Path $scratch 'push.pid') -Value $process.Id -Encoding ascii
"started pid $($process.Id)"
