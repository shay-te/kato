# Turn on Windows code signing for this release build, when it is configured.
#
# Runs on the windows-latest job of .github/workflows/desktop-release.yaml,
# before `tauri build`. Everything it needs comes from the workflow's
# environment: the repository variable WINDOWS_SIGNING and, for 'esigner'
# (an SSL.com certificate kept in SSL.com's eSigner cloud HSM), the secrets
# ESIGNER_USERNAME, ESIGNER_PASSWORD, ESIGNER_CREDENTIAL_ID, ESIGNER_TOTP_SECRET.
#
# Not configured: the build goes on UNSIGNED, said in a warning on the run —
# exactly what every build did before. Configured but a secret missing: it
# STOPS, naming the missing ones, instead of quietly shipping unsigned files.
#
# Configured, it writes a Tauri config fragment that holds only the sign
# command (no secret ever goes into a file) and hands it to `tauri build` with
# --config. Tauri then runs sign-windows.ps1 on every file it ships: the app,
# the sidecar, the installer's plugins, the uninstaller and the installer —
# and makes the updater's signatures after that, over the signed files.

$ErrorActionPreference = 'Stop'

# Jsign signs Windows files with keys in cloud HSMs, SSL.com eSigner included.
# Pinned by version AND checksum (the same jar is on GitHub and Maven Central).
$JsignVersion = '7.6'
$JsignUrl = "https://github.com/ebourg/jsign/releases/download/$JsignVersion/jsign-$JsignVersion.jar"
$JsignSha256 = '6cf58aa080e7ff82656f320cff8d4a3bff30408df8766806dcba3740dd839fa5'

function Write-StepOutput([string] $name, [string] $value) {
    Add-Content -LiteralPath $env:GITHUB_OUTPUT -Value "$name=$value"
}

$method = "$env:WINDOWS_SIGNING".Trim().ToLowerInvariant()
if (-not $method) {
    Write-Output ('::warning title=Windows installer is UNSIGNED::Windows code signing is not configured ' +
        '(repository variable WINDOWS_SIGNING), so this build is unsigned and Windows will warn the ' +
        'people who download it. See desktop/signing/README.md.')
    Write-StepOutput 'tauri_args' ''
    Write-StepOutput 'signed' 'false'
    exit 0
}
if ($method -ne 'esigner') {
    throw "WINDOWS_SIGNING is '$method'; the only method set up is 'esigner' (desktop/signing/README.md)."
}

$required = 'ESIGNER_USERNAME', 'ESIGNER_PASSWORD', 'ESIGNER_CREDENTIAL_ID', 'ESIGNER_TOTP_SECRET'
$missing = @($required | Where-Object { -not [Environment]::GetEnvironmentVariable($_) })
if ($missing.Count) {
    throw "WINDOWS_SIGNING is 'esigner' but these repository secrets are not set: $($missing -join ', ')"
}
if (-not (Get-Command java -ErrorAction SilentlyContinue)) {
    throw 'Jsign needs Java, and none is on PATH (the workflow sets up Java 17 before this step).'
}

$jar = Join-Path $env:RUNNER_TEMP "jsign-$JsignVersion.jar"
Invoke-WebRequest -Uri $JsignUrl -OutFile $jar -UseBasicParsing
$hash = (Get-FileHash -LiteralPath $jar -Algorithm SHA256).Hash.ToLowerInvariant()
if ($hash -ne $JsignSha256) {
    throw "jsign-$JsignVersion.jar has SHA-256 $hash, not the pinned $JsignSha256 — not using it."
}

# Absolute paths: NSIS runs the uninstaller's signing from its own folder.
# `pwsh` by name (no spaces), and the repository's path has none either.
$signScript = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot 'sign-windows.ps1')).Path
$fragment = @{
    bundle = @{
        windows = @{
            signCommand = @{
                cmd  = 'pwsh'
                args = @('-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', $signScript, '%1')
            }
        }
    }
}
$configPath = Join-Path $env:RUNNER_TEMP 'tauri.signing.conf.json'
$fragment | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $configPath -Encoding utf8

Add-Content -LiteralPath $env:GITHUB_ENV -Value "KATO_JSIGN_JAR=$jar"
Write-StepOutput 'tauri_args' "--config $configPath"
Write-StepOutput 'signed' 'true'
Write-Output "Windows code signing is on (SSL.com eSigner via Jsign $JsignVersion)."
