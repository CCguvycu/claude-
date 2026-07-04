# run-ollama.ps1 — launch this Claude Code build against a local Ollama server.
#
# Usage:
#   .\run-ollama.ps1 "your prompt here"      # one-shot (-p print mode)
#   .\run-ollama.ps1                          # interactive session
#   $env:OLLAMA_MODEL="qwen2.5-coder:7b"; .\run-ollama.ps1 "..."   # pick a model
#
# Requires: `ollama serve` running and at least one model pulled (`ollama pull llama3.1`).

param(
  [Parameter(ValueFromRemainingArguments = $true)]
  [string[]] $PromptArgs
)

$ErrorActionPreference = 'Stop'

# --- Backend selection: route all model calls to local Ollama ---------------
$env:CLAUDE_CODE_USE_OLLAMA = '1'
if (-not $env:OLLAMA_BASE_URL) { $env:OLLAMA_BASE_URL = 'http://localhost:11434' }
if (-not $env:OLLAMA_MODEL)    { $env:OLLAMA_MODEL    = 'llama3.1:latest' }
if (-not $env:OLLAMA_NUM_CTX)  { $env:OLLAMA_NUM_CTX  = '32768' }

# Dummy key so first-run onboarding doesn't demand an Anthropic login.
# (The Ollama backend never actually uses it.)
if (-not $env:ANTHROPIC_API_KEY) { $env:ANTHROPIC_API_KEY = 'ollama' }

# Keep everything local: no telemetry, no auto-update, no error reporting.
$env:CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC = '1'
$env:DISABLE_TELEMETRY        = '1'
$env:DISABLE_ERROR_REPORTING  = '1'
$env:DISABLE_AUTOUPDATER      = '1'

# --- Preflight checks -------------------------------------------------------
try {
  $tags = Invoke-RestMethod -Uri "$($env:OLLAMA_BASE_URL)/api/tags" -TimeoutSec 3
} catch {
  Write-Host "ERROR: Cannot reach Ollama at $($env:OLLAMA_BASE_URL)." -ForegroundColor Red
  Write-Host "Start it with:  ollama serve" -ForegroundColor Yellow
  exit 1
}

$have = $tags.models | ForEach-Object { $_.name }
if ($have -notcontains $env:OLLAMA_MODEL) {
  Write-Host "WARNING: model '$($env:OLLAMA_MODEL)' is not pulled." -ForegroundColor Yellow
  Write-Host "Available: $($have -join ', ')" -ForegroundColor Yellow
  Write-Host "Pull it with:  ollama pull $($env:OLLAMA_MODEL)" -ForegroundColor Yellow
}

Write-Host "→ Claude Code (local) via Ollama: model=$($env:OLLAMA_MODEL) ctx=$($env:OLLAMA_NUM_CTX)" -ForegroundColor Cyan

# --- Launch -----------------------------------------------------------------
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if ($PromptArgs -and $PromptArgs.Count -gt 0) {
  # Print mode: feed empty stdin so the CLI's stdin-peek returns immediately
  # instead of waiting 3s (only matters when stdin isn't an interactive TTY).
  $null | & bun run "$scriptDir/src/entrypoints/cli.tsx" -p @PromptArgs
} else {
  & bun run "$scriptDir/src/entrypoints/cli.tsx"
}
