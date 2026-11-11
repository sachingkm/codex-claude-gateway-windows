param([switch]$BootstrapOnly)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$dataRoot = if ($env:CODEX_CLAUDE_DATA_DIR) { $env:CODEX_CLAUDE_DATA_DIR } else { Join-Path $env:LOCALAPPDATA 'CodexClaudeGateway' }
$keyPath = Join-Path $dataRoot 'anthropic_key_gpt.dpapi'
$settings = Get-Content -LiteralPath (Join-Path $dataRoot 'settings_gpt.json') -Raw | ConvertFrom-Json
$env:CODEX_CLAUDE_DATA_DIR = $dataRoot
$env:CODEX_DEFAULT_MODEL = $settings.default_model
$python = Join-Path $dataRoot '.venv\Scripts\python.exe'
$mutex = [Threading.Mutex]::new($false, 'Local\CodexClaudePickerGateway_gpt')
$ownsMutex = $false
try {
    try { $ownsMutex = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $ownsMutex = $true }
    if (-not $ownsMutex) { Write-Output 'Claude picker gateway is already running.'; exit 0 }
    $listener = if (-not $BootstrapOnly) { Get-NetTCPConnection -State Listen -LocalPort 42851 -ErrorAction SilentlyContinue }
    if ($listener) {
        $health = Invoke-RestMethod -Uri 'http://127.0.0.1:42851/health' -TimeoutSec 3
        if ($health.gateway -eq 'codex-claude-picker-gpt') { Write-Output 'Claude picker gateway is already running.'; exit 0 }
        throw 'Port 42851 is already in use; no duplicate gateway started.'
    }
    if (-not (Test-Path -LiteralPath $keyPath)) { throw 'Encrypted Anthropic key missing.' }
    if (-not (Test-Path -LiteralPath $python)) { throw 'Gateway Python environment missing.' }
    $secure = ConvertTo-SecureString -String ([System.IO.File]::ReadAllText($keyPath).Trim())
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { $env:ANTHROPIC_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
    $env:ANTHROPIC_WORKSPACE_ID = [string]$settings.workspace_id
    $env:LITELLM_LOCAL_MODEL_COST_MAP = 'true'
    $env:PYTHONIOENCODING = 'utf-8'
    $env:CODEX_GATEWAY_TEST_MODE = '0'
    $env:CODEX_CLAUDE_PORT = '42851'
    $env:CODEX_CLIENT_VERSION = '0.167.0-alpha.3.2'
    $installed = Get-AppxPackage -Name OpenAI.Codex -ErrorAction SilentlyContinue | Sort-Object Version -Descending | Select-Object -First 1
    if ($installed) {
        $codexBinary = Join-Path $installed.InstallLocation 'app\resources\codex.exe'
        if (Test-Path -LiteralPath $codexBinary) {
            $versionText = & $codexBinary --version
            if ($versionText -match '^codex-cli\s+([^\s]+)') { $env:CODEX_CLIENT_VERSION = $Matches[1] }
        }
    }
    try {
        if ($BootstrapOnly) {
            & $python (Join-Path $root 'bootstrap_gpt.py')
            if ($LASTEXITCODE -ne 0) { throw 'Model bootstrap failed. Codex configuration has not been changed.' }
            return
        }
        Write-Output 'Claude picker gateway starting on 127.0.0.1:42851.'
        & $python -m uvicorn gateway_gpt:app --app-dir $root --host 127.0.0.1 --port 42851 --no-access-log --ws websockets
        if ($LASTEXITCODE -ne 0) { throw "Gateway exited with code $LASTEXITCODE." }
    } finally {
        Remove-Item Env:ANTHROPIC_API_KEY -ErrorAction SilentlyContinue
        Remove-Item Env:ANTHROPIC_WORKSPACE_ID -ErrorAction SilentlyContinue
    }
} finally {
    if ($ownsMutex) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
