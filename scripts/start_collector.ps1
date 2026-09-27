param(
    [Parameter(Mandatory=$true)][ValidateSet('official-laws','official-courts','official-law-pages','official-law-recovery','official-law-nebraska','county-sites','official-law-resume3','official-law-quick','official-law-state-rules','county-entries-continuation','county-registry-continuation','county-local-documents','county-wa-rules','firecrawl','firecrawl-batch','ocr','ocr-courts','ocr-laws','ocr-law-recovery','ocr-law-pages')][string]$Job,
    [int]$Pages = 0,
    [ValidateRange(0,3600)][int]$RunSeconds = 0,
    [string]$PythonPath,
    [string]$SelectionFile,
    [switch]$OnceBatch
)
$ErrorActionPreference = 'Stop'
$collectionRoot = Split-Path -Parent $PSScriptRoot
$pythonConsole = if ($PythonPath) { (Resolve-Path -LiteralPath $PythonPath -ErrorAction Stop).Path } else { (Get-Command python -ErrorAction Stop).Source }
$pythonHidden = Join-Path (Split-Path -Parent $pythonConsole) 'pythonw.exe'
$wrapperPath = Join-Path $PSScriptRoot 'run_collector.py'
$continuationRoots = @{
    'official-law-resume3' = 'official_law_resume_pass3_20260913'
    'official-law-quick' = 'official_law_resume_quick_20260914'
    'official-law-state-rules' = 'official_law_state_rules_followup_20260914'
    'county-entries-continuation' = 'county_entries_continuation_20260913'
    'county-registry-continuation' = 'county_registry_continuation_20260913'
    'county-local-documents' = 'county_local_documents_20260914'
    'county-wa-rules' = 'county_local_rules_washington_20260914'
}
if ($continuationRoots.ContainsKey($Job)) {
    $rootName = $continuationRoots[$Job]
    $existingContinuation = Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" | Where-Object {
        ($_.CommandLine -match 'corpus_crawler\.py' -and $_.CommandLine -match [regex]::Escape($rootName)) -or
        ($_.CommandLine -like ('*' + $wrapperPath + '*') -and $_.CommandLine -match ('"\s+' + [regex]::Escape($Job) + '(\s|$)'))
    } | Select-Object -First 1
    if ($existingContinuation) { [pscustomobject]@{job=$Job;pid=$existingContinuation.ProcessId;already_running=$true}; return }
}
$existingWorker = Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe'" | Where-Object { $_.CommandLine -like ('*' + $wrapperPath + '*') -and $_.CommandLine -match ('"\s+' + [regex]::Escape($Job) + '(\s|$)') } | Select-Object -First 1
if ($existingWorker) { [pscustomobject]@{job=$Job;pid=$existingWorker.ProcessId;already_running=$true}; return }
$startupInfo = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ShowWindow=[uint16]0}
$collectorCommand = '"' + $pythonHidden + '" "' + $wrapperPath + '" ' + $Job
if ($RunSeconds -gt 0) {
    if (-not $continuationRoots.ContainsKey($Job)) { throw 'RunSeconds requires a finite continuation job' }
    $collectorCommand += ' --seconds ' + $RunSeconds
}
if ($Pages -gt 0) { $collectorCommand += ' --pages ' + $Pages }
if ($SelectionFile) {
    if ($Job -ne 'firecrawl-batch') { throw 'SelectionFile requires firecrawl-batch' }
    $selectedUrlFile = (Resolve-Path -LiteralPath $SelectionFile -ErrorAction Stop).Path
    $collectorCommand += ' --selection-file "' + $selectedUrlFile + '"'
}
if ($OnceBatch) {
    if ($Job -ne 'firecrawl-batch') { throw 'OnceBatch requires firecrawl-batch' }
    $collectorCommand += ' --once-batch'
}
if ($Job -in @('firecrawl','firecrawl-batch')) {
    # Resolve Windows packaged-app filesystem redirection before leaving that app.
    $credentialFile = (& $pythonConsole -c "from pathlib import Path; print((Path.home()/'AppData/Local/LegalCorpusArchive/firecrawl-key.dpapi').resolve(strict=True))").Trim()
    if ($LASTEXITCODE -ne 0 -or -not $credentialFile) { throw 'Private Firecrawl credential file was not found' }
    $collectorCommand += ' --credential-file "' + $credentialFile + '"'
}
$created = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine=$collectorCommand;CurrentDirectory=$collectionRoot;ProcessStartupInformation=$startupInfo}
if ($created.ReturnValue -ne 0) { throw ('Worker launch failed: Win32 return ' + $created.ReturnValue) }
[pscustomobject]@{job=$Job;pid=$created.ProcessId;hidden=$true;command=$collectorCommand}
