param([string]$WorkspaceId = '', [string]$DefaultModel = 'gpt-6.1-sol')
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$dataRoot = if ($env:CODEX_CLAUDE_DATA_DIR) { $env:CODEX_CLAUDE_DATA_DIR } else { Join-Path $env:LOCALAPPDATA 'CodexClaudeGateway' }
New-Item -ItemType Directory -Path $dataRoot -Force | Out-Null
$python = Join-Path $dataRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { py -3.13 -m venv (Join-Path $dataRoot '.venv'); if ($LASTEXITCODE) { throw 'Python 3.13 is required.' } }
& $python -m pip install -r (Join-Path $root 'requirements.txt')
if ($LASTEXITCODE) { throw 'Dependency installation failed.' }
$keyPath = Join-Path $dataRoot 'anthropic_key_gpt.dpapi'
if (-not (Test-Path -LiteralPath $keyPath)) {
    $secure = Read-Host 'Enter Anthropic API key (hidden)' -AsSecureString
    [IO.File]::WriteAllText($keyPath, (ConvertFrom-SecureString $secure))
}
@{workspace_id=$WorkspaceId;default_model=$DefaultModel} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $dataRoot 'settings_gpt.json') -Encoding UTF8
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'start_claude_picker_gpt.ps1') -BootstrapOnly
if ($LASTEXITCODE) { throw 'Bootstrap failed; Codex config has not been edited.' }
Write-Output 'Setup complete. Follow README to back up and configure Codex, then start the gateway.'
