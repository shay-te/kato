# Fail the release when anything it ships on Windows is not signed and
# timestamped.
#
# Runs on the windows-latest job after `tauri build`, only when signing is on.
# It checks what a person actually gets, three ways:
#   1. the installer file itself;
#   2. everything inside it (the app, the sidecar, the installer's plugin
#      DLLs), read with 7-Zip — nothing runs;
#   3. what it installs: a silent install into a temporary folder, which is the
#      only way to see the uninstaller (NSIS writes it at install time).
# Every .exe and .dll must carry a valid Authenticode signature WITH a
# timestamp, and — when WINDOWS_SIGNING_PUBLISHER is set — from that publisher.

param(
    [string] $Publisher = ''
)

$ErrorActionPreference = 'Stop'

$bundle = Join-Path $PSScriptRoot '..\src-tauri\target\release\bundle\nsis'
$installer = Get-ChildItem -LiteralPath $bundle -Filter '*-setup.exe' -File | Select-Object -First 1
if (-not $installer) { throw "no NSIS installer in $bundle" }

$work = Join-Path $env:RUNNER_TEMP 'kato-signature-check'
Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
$extracted = Join-Path $work 'extracted'
$installed = Join-Path $work 'installed'

& 7z x $installer.FullName "-o$extracted" -y | Out-Null
if ($LASTEXITCODE -ne 0) { throw "7-Zip could not read $($installer.Name)" }

# /D must come last and unquoted; the runner's temp folder has no spaces.
$setup = Start-Process -FilePath $installer.FullName -ArgumentList '/S', "/D=$installed" -Wait -PassThru
if ($setup.ExitCode -ne 0) { throw "the silent install exited with code $($setup.ExitCode)" }

$shipped = @($installer) + @(
    Get-ChildItem -LiteralPath $extracted, $installed -Recurse -File |
        Where-Object { $_.Extension -in '.exe', '.dll' }
)

# Never pass on an empty look: the files that matter must be there to check.
foreach ($expected in 'kato-sidecar*.exe', 'uninstall*.exe') {
    if (-not ($shipped | Where-Object { $_.Name -like $expected -and $_.FullName.StartsWith($installed) })) {
        throw "the installer did not install a file like '$expected' — nothing to check it against"
    }
}

$problems = foreach ($file in $shipped) {
    $signature = Get-AuthenticodeSignature -LiteralPath $file.FullName
    $why = if ($signature.Status -ne 'Valid') {
        "not signed ($($signature.Status))"
    } elseif (-not $signature.TimeStamperCertificate) {
        'signed but not timestamped'
    } elseif ($Publisher -and $signature.SignerCertificate.Subject -notmatch "CN=`"?$([regex]::Escape($Publisher))") {
        "signed by $($signature.SignerCertificate.Subject), not $Publisher"
    }
    # Only problems are this loop's output; the passing files go to the log.
    if ($why) { "$($file.FullName.Replace($work, '<check>')): $why" }
    else { Write-Host "ok  $($file.Name)  ($($signature.SignerCertificate.Subject))" }
}

if ($problems) {
    foreach ($problem in $problems) { Write-Output "::error title=Unsigned Windows file::$problem" }
    throw "$(@($problems).Count) shipped Windows file(s) are not properly signed — do not publish this release."
}
Write-Output "All $($shipped.Count) shipped Windows files are signed and timestamped."
