$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$dataRoot = if ($env:CODEX_CLAUDE_DATA_DIR) { $env:CODEX_CLAUDE_DATA_DIR } else { Join-Path $env:LOCALAPPDATA 'CodexClaudeGateway' }
$launcher = Join-Path $root 'start_claude_picker_gpt.ps1' 
$restart = Join-Path $root 'restart_claude_picker_gpt.ps1'
$python = Join-Path $dataRoot '.venv\Scripts\python.exe'
$taskName = 'CodexClaudePickerGateway_gpt'
$health = Invoke-RestMethod -Uri 'http://127.0.0.1:42851/health' -TimeoutSec 5
if (-not $health.ready -or $health.version -ne 2) { throw 'Corrected gateway must be healthy before installing automatic startup.' }
$existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existingTask) {
    if ($existingTask.Actions.Count -ne 1 -or $existingTask.Actions[0].Arguments -notlike "*$launcher*") { throw 'An unexpected task already uses this name; nothing changed.' }
    Write-Output 'The gateway automatic startup task already exists.'
    exit 0
}
$hostExe = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$action = New-ScheduledTaskAction -Execute $hostExe -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$launcher`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew -Hidden
try {
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'Starts the local GPT and Claude gateway for Codex using the Windows-encrypted Anthropic key.' | Out-Null
} catch {
    if ($_.CategoryInfo.Category -ne 'PermissionDenied' -and $_.Exception.Message -notmatch 'Access is denied|0x80070005') { throw }
    $runPath = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
    $runCommand = '`"' + $hostExe + '`" -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"' + $launcher + '`"'
    $runCommand = $runCommand.Replace('`"', '"')
    $existingValue = Get-ItemPropertyValue -LiteralPath $runPath -Name $taskName -ErrorAction SilentlyContinue
    if ($existingValue -and $existingValue -ne $runCommand) { throw 'An unexpected Windows startup entry already uses this name; nothing overwritten.' }
    New-ItemProperty -LiteralPath $runPath -Name $taskName -PropertyType String -Value $runCommand -Force | Out-Null
    if ((Get-ItemPropertyValue -LiteralPath $runPath -Name $taskName) -ne $runCommand) { throw 'Windows startup entry verification failed.' }
    Write-Output 'Automatic startup configured for your Windows sign-in. The running background gateway stays active.'
    Write-Output 'Windows did not permit a scheduled task; a verified per-user startup entry was used. No password or API key was stored in that entry.'
    exit 0
}
try {
    $listeners = Get-NetTCPConnection -State Listen -LocalPort 42851 -ErrorAction SilentlyContinue
    foreach ($processId in @($listeners.OwningProcess | Sort-Object -Unique)) {
        $existing = Get-CimInstance Win32_Process -Filter "ProcessId = $processId"
        if ($existing.ExecutablePath -ne $python -or $existing.CommandLine -notmatch 'gateway_gpt:app' -or $existing.CommandLine -notmatch '--port\s+42851') { throw 'Unexpected process on gateway port; it was not stopped.' }
        Stop-Process -Id $processId -ErrorAction Stop
    }
    Start-Sleep -Seconds 2
    Start-ScheduledTask -TaskName $taskName
    for ($attempt = 0; $attempt -lt 40; $attempt++) {
        Start-Sleep -Milliseconds 500
        try {
            $health = Invoke-RestMethod -Uri 'http://127.0.0.1:42851/health' -TimeoutSec 1
            $task = Get-ScheduledTask -TaskName $taskName
            if ($health.ready -and $health.version -eq 2 -and $task.State -eq 'Running') {
                Write-Output 'Automatic startup installed and running. Gateway will start quietly at your Windows sign-in.'
                Write-Output 'The saved Anthropic key is used automatically; its Anthropic expiry still applies.'
                exit 0
            }
        } catch { }
    }
    throw 'Scheduled gateway did not become healthy.'
} catch {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    & powershell -NoProfile -ExecutionPolicy Bypass -File $restart
    throw 'Automatic startup did not pass verification. The background gateway restart was attempted; the startup task was removed.'
}
