# PowerShell port of the POSIX Makefile so Windows operators can run
# the same workflow as macOS/Linux:
#
#     .\make.ps1 bootstrap
#     .\make.ps1 compose-up
#     .\make.ps1 test
#     .\make.ps1 sandbox-verify
#
# Mirrors every target in ``Makefile`` one-to-one. Keep the two
# files in sync when adding new targets — the Makefile is the
# canonical reference for what each target does.
#
# PowerShell's default execution policy blocks unsigned scripts.
# Three ways to use this file:
#   1. Bypass once:   powershell -ExecutionPolicy Bypass -File .\make.ps1 <target>
#   2. Per-user once: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
#   3. Use make.bat   (companion file, no policy needed at all)

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Target = '',
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Rest = @()
)

$ErrorActionPreference = 'Stop'

# Resolve which python to use. ``bootstrap`` runs before the venv
# exists so it must use system python; everything else uses the
# venv's pinned interpreter.
$Python = 'python'
$VenvPython = if (Test-Path '.venv\Scripts\python.exe') {
    '.venv\Scripts\python.exe'
} else {
    'python'
}

function Invoke-Step([string]$Exe, [string[]]$Argv) {
    & $Exe @Argv
    exit $LASTEXITCODE
}

function Show-Usage {
    Write-Host 'Usage: .\make.ps1 <target>'
    Write-Host ''
    Write-Host 'Targets:'
    Write-Host '  bootstrap            Install Python deps + build the planning UI'
    Write-Host '  bootstrap --skip-tests'
    Write-Host '                       Same, but skip the unit-test sanity run'
    Write-Host '  configure            Generate .env interactively'
    Write-Host '  doctor               Validate full env config'
    Write-Host '  doctor-agent         Validate just the agent backend'
    Write-Host '  doctor-openhands     Validate just the openhands config'
    Write-Host '  test                 Run the unit-test suite'
    Write-Host '  run                  Start kato (alias: compose-up)'
    Write-Host '  compose-up           Start kato'
    Write-Host '  sandbox-build        Build the hardened Docker sandbox image'
    Write-Host '  sandbox-login        Interactive Claude login inside the sandbox'
    Write-Host '  sandbox-verify       End-to-end smoke test of the sandbox'
}

switch -Exact ($Target) {
    'bootstrap'        { Invoke-Step $Python      (@('scripts\bootstrap.py') + $Rest) }
    'configure'        { Invoke-Step $VenvPython  @('scripts\generate_env.py', '--output', '.env') }
    'doctor'           { Invoke-Step $VenvPython  @('-m', 'kato.validate_env', '--env-file', '.env', '--mode', 'all') }
    'doctor-agent'     { Invoke-Step $VenvPython  @('-m', 'kato.validate_env', '--env-file', '.env', '--mode', 'agent') }
    'doctor-openhands' { Invoke-Step $VenvPython  @('-m', 'kato.validate_env', '--env-file', '.env', '--mode', 'openhands') }
    'test'             { Invoke-Step $VenvPython  @('-m', 'unittest', 'discover', '-s', 'tests') }
    'run'              { Invoke-Step $VenvPython  @('scripts\run_local.py') }
    'compose-up'       { Invoke-Step $VenvPython  @('scripts\run_local.py') }
    'sandbox-build'    { Invoke-Step $VenvPython  @('-c', 'from kato.sandbox.manager import build_image; build_image()') }
    'sandbox-verify'   { Invoke-Step $VenvPython  @('-m', 'kato.sandbox.verify') }
    'sandbox-login'    { Invoke-Step $VenvPython  @('-c', 'from kato.sandbox.manager import ensure_image, login_command; import subprocess, sys; ensure_image(); sys.exit(subprocess.call(login_command()))') }
    ''                 { Show-Usage; exit 1 }
    default {
        Write-Host "Unknown target: $Target" -ForegroundColor Red
        Write-Host ''
        Show-Usage
        exit 1
    }
}
