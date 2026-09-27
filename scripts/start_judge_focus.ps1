param(
    [Parameter(Mandatory=$true)][string]$SelectionFile,
    [ValidateRange(1,3600)][int]$RunSeconds = 900,
    [ValidateRange(1,100)][int]$BatchSize = 50,
    [ValidateRange(1,1400)][int]$Reserve = 100
)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskSelection = (Resolve-Path -LiteralPath $SelectionFile).Path
$taskPython = 'C:\Users\firas\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$taskWrapper = Join-Path $PSScriptRoot 'run_judge_focus.py'
$taskExisting = Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" | Where-Object {
    $_.CommandLine -match 'run_judge_focus\.py|run_collector\.py.+firecrawl|firecrawl_batch_worker\.py|firecrawl_worker\.py'
}
if ($taskExisting) { throw 'An existing Firecrawl/judge worker process must be inspected before another launch.' }
$taskStartup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ShowWindow=[uint16]0}
$taskCommand = '"' + $taskPython + '" "' + $taskWrapper + '" --selection-file "' + $taskSelection + '" --seconds ' + $RunSeconds + ' --batch-size ' + $BatchSize + ' --reserve ' + $Reserve
$taskCreated = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine=$taskCommand;CurrentDirectory=$taskRoot;ProcessStartupInformation=$taskStartup}
if ($taskCreated.ReturnValue -ne 0) { throw ('Judge worker launch failed: ' + $taskCreated.ReturnValue) }
[pscustomobject]@{pid=$taskCreated.ProcessId;hidden=$true;selection=$taskSelection;seconds=$RunSeconds;batchSize=$BatchSize;reserve=$Reserve}
