$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$dataRoot = if ($env:CODEX_CLAUDE_DATA_DIR) { $env:CODEX_CLAUDE_DATA_DIR } else { Join-Path $env:LOCALAPPDATA 'CodexClaudeGateway' }
$launcher = Join-Path $root 'start_claude_picker_gpt.ps1' 
$python = Join-Path $dataRoot '.venv\Scripts\python.exe'
$taskScratch = Join-Path $dataRoot 'logs'
New-Item -ItemType Directory -Path $taskScratch -Force | Out-Null
$listeners = Get-NetTCPConnection -State Listen -LocalPort 42851 -ErrorAction SilentlyContinue
foreach ($processId in @($listeners.OwningProcess | Sort-Object -Unique)) {
    $existing = Get-CimInstance Win32_Process -Filter "ProcessId = $processId"
    if ($existing.ExecutablePath -ne $python -or $existing.CommandLine -notmatch 'gateway_gpt:app' -or $existing.CommandLine -notmatch '--port\s+42851') { throw 'Port 42851 belongs to an unexpected process; nothing stopped.' }
    Stop-Process -Id $processId -ErrorAction Stop
}
$stamp = Get-Date -Format yyyyMMdd_HHmmss
$outLog = Join-Path $taskScratch "gateway_background_stdout_${stamp}_gpt.log"
$errLog = Join-Path $taskScratch "gateway_background_stderr_${stamp}_gpt.log"
$hostExe = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$child = Start-Process -FilePath $hostExe -ArgumentList @('-NoProfile','-NonInteractive','-WindowStyle','Hidden','-ExecutionPolicy','Bypass','-File',('"' + $launcher + '"')) -WindowStyle Hidden -RedirectStandardOutput $outLog -RedirectStandardError $errLog -PassThru
for ($attempt = 0; $attempt -lt 40; $attempt++) {
    Start-Sleep -Milliseconds 500
    try {
        $health = Invoke-RestMethod -Uri 'http://127.0.0.1:42851/health' -TimeoutSec 1
        if ($health.ready -and $health.gateway -eq 'codex-claude-picker-gpt' -and $health.version -eq 2) {
            Write-Output 'Updated gateway ready in the background. Saved key unlocked; no key entry needed.'
            Write-Output 'You can close this PowerShell window. Automatic Windows startup is not enabled yet.'
            exit 0
        }
    } catch { }
    if ($child.HasExited) { throw "Gateway stopped. Error log: $errLog" }
}
throw "Gateway did not become ready. Error log: $errLog"
