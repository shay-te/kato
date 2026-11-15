# Sign and timestamp ONE Windows file — Tauri's signCommand (%1 = the file).
#
# Tauri calls this for every file it ships on Windows: the app and the sidecar
# before they are bundled, the installer's plugins, the uninstaller (from
# inside makensis) and the finished installer. Any failure fails `tauri build`:
# an unsigned file is never shipped by accident.
#
# The key never leaves SSL.com's eSigner cloud HSM: Jsign sends the file's
# digest there, signed in with the account (ESIGNER_USERNAME/PASSWORD), the
# certificate (ESIGNER_CREDENTIAL_ID) and a one-time code it computes from the
# TOTP secret (ESIGNER_TOTP_SECRET). All four come from repository secrets via
# the workflow's environment — never from a file.

param(
    [Parameter(Mandatory = $true)]
    [string] $File
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $File -PathType Leaf)) {
    throw "nothing to sign at '$File'"
}
if (-not $env:KATO_JSIGN_JAR -or -not (Test-Path -LiteralPath $env:KATO_JSIGN_JAR)) {
    throw 'KATO_JSIGN_JAR is not set: enable-windows-signing.ps1 must run before tauri build.'
}

# Two timestamp authorities, the second used only when the first does not
# answer: a signature with no timestamp stops being valid when the certificate
# expires, so it is never left out.
$timestampers = 'http://ts.ssl.com,http://timestamp.digicert.com'

$jsign = @(
    '-jar', $env:KATO_JSIGN_JAR,
    '--storetype', 'ESIGNER',
    '--storepass', "$($env:ESIGNER_USERNAME)|$($env:ESIGNER_PASSWORD)",
    '--alias', $env:ESIGNER_CREDENTIAL_ID,
    '--keypass', $env:ESIGNER_TOTP_SECRET,
    '--alg', 'SHA-256',
    '--tsaurl', $timestampers,
    '--tsmode', 'RFC3161',
    # Tauri signs the app again after patching it for each package type: the
    # old signature is replaced, not stacked under the new one.
    '--replace',
    $File
)

# A one-time code is good for one 30-second window, and Tauri signs about nine
# files in a row: a code used twice can be refused. Waiting out the window and
# trying again covers that, and a passing network error.
$attempts = 3
for ($attempt = 1; $attempt -le $attempts; $attempt++) {
    & java @jsign
    if ($LASTEXITCODE -eq 0) {
        Write-Output "signed $File"
        exit 0
    }
    if ($attempt -lt $attempts) {
        Write-Output "signing $File failed (attempt $attempt of $attempts) — trying again in 31s"
        Start-Sleep -Seconds 31
    }
}
throw "could not sign '$File' after $attempts attempts"
