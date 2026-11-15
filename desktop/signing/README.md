# Signing the Windows build

An unsigned installer downloaded from GitHub makes Windows warn: SmartScreen's "Windows protected your PC", and sometimes the browser's "not commonly downloaded". Signing every file Kato ships on Windows, with a certificate in a real person's name, is the honest fix. This folder does that inside the release build, and checks that it worked.

**Until you set it up, nothing changes.** Release builds stay unsigned, with a warning on the workflow run that says so. Local builds and `npm run build` never sign.

## Which certificate, and why

| Option | Can an individual in Israel get it? | Works unattended in CI? | Rough cost per year |
|---|---|---|---|
| **Azure Artifact Signing** (formerly Trusted Signing) | **No.** Individual developers must be in the US or Canada. Israeli *organizations* became eligible in July 2026 | Yes | $120 ($9.99/month) |
| **SSL.com IV certificate with eSigner (chosen)** | **Yes**: an individual (IV) certificate, no company needed | **Yes**: a stored TOTP secret | ~$309 ($129 certificate + $180 eSigner, 240 signings) |
| DigiCert KeyLocker, GlobalSign | No: organizations only | Yes | $250–$1,000 |
| Certum Open Source (SimplySign cloud) | Yes | Only through unofficial workarounds | ~€49 |
| SignPath Foundation (free) | Declined the project on 2026-10-13 | Yes | Free |

Sources: Microsoft's [Artifact Signing quickstart](https://learn.microsoft.com/en-us/azure/artifact-signing/quickstart) ("Individual developers must be located in the United States or Canada") and [code signing options](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/code-signing-options); [SSL.com IV code signing](https://www.ssl.com/products/software-integrity/code-signing/iv/) and [eSigner pricing](https://www.ssl.com/guide/esigner-pricing-for-code-signing/). Prices change, so check them before buying.

Since June 2023, a code signing key must live in a hardware security module. A plain `.pfx` file in CI is no longer possible for a new certificate. With eSigner, the key stays in SSL.com's cloud HSM. The build sends each file's digest there and gets the signature back.

**If you register a company:** Artifact Signing is the cheaper, Microsoft-native route for an Israeli organization. Only `sign-windows.ps1` and the secrets would change (Tauri's docs show the `artifact-signing-cli` command). The rest of this setup stays.

## What to buy and set up (once)

1. **Buy an SSL.com IV (Individual Validation) code signing certificate** with **eSigner** cloud signing ([ssl.com/products/software-integrity/code-signing/iv](https://www.ssl.com/products/software-integrity/code-signing/iv/)). Pick eSigner as the key storage, not a USB token.
2. **Validate your identity.** SSL.com asks for a government ID (name and address must match) and takes about 3–5 working days. The certificate is issued in your legal name, and that name is what Windows shows as the publisher.
3. **Subscribe to an eSigner plan.** Each release signs about **9 files**: the app, the sidecar, five installer plugin DLLs, the uninstaller and the installer. Tier 1 annual (240 signings) covers about 26 releases a year. A retried signature counts as a signing too.
4. **Enroll eSigner for automation and keep the TOTP secret.** In your SSL.com account, open the certificate order's signing credentials and enroll it in eSigner. Keep the **TOTP secret**, the base64 text shown with the QR code, the same way you'd keep a password. It is what lets CI sign with no phone. See SSL.com's [CodeSignTool guide](https://www.ssl.com/guide/esigner-codesigntool-command-guide/) and their "Automate eSigner EV Code Signing" article.
5. **Find the credential ID** (a UUID): run `CodeSignTool get_credential_ids -username=… -password=…` once on your own machine, or copy it from the eSigner page of your account.

## Turn it on in GitHub

Open **Settings → Secrets and variables → Actions** in the repository:

| Kind | Name | Value |
|---|---|---|
| Variable | `WINDOWS_SIGNING` | `esigner` |
| Variable | `WINDOWS_SIGNING_PUBLISHER` | optional: the name on the certificate, e.g. `Shay Tessler`. The check then also fails a file signed by anyone else |
| Secret | `ESIGNER_USERNAME` | your SSL.com account username |
| Secret | `ESIGNER_PASSWORD` | its password |
| Secret | `ESIGNER_CREDENTIAL_ID` | the credential ID from step 5 |
| Secret | `ESIGNER_TOTP_SECRET` | the TOTP secret from step 4 |

Secrets are stored only as GitHub Actions secrets. They reach the sign command only through the environment, and no file in the repository or on the runner ever holds them. (`kato.p12` in the repository root is gitignored and must stay out of git.)

The next `desktop-v*` tag then builds signed. If `WINDOWS_SIGNING` is set and a secret is missing, the Windows job stops and names the missing ones, rather than shipping unsigned.

## What happens in the release build

`.github/workflows/desktop-release.yaml`, Windows job only (macOS and Linux are untouched):

1. **Install kato + PyInstaller.** PyInstaller's bootloader is built from source (see *Defender false positives* below).
2. **Windows code signing, when configured** (`enable-windows-signing.ps1`). It downloads [Jsign](https://ebourg.github.io/jsign/) 7.6, pinned by checksum, and writes a small Tauri config fragment to the runner's temp folder. The fragment holds only the sign command, which runs `sign-windows.ps1 <file>`.
3. **`tauri build --config <that fragment>`.** Tauri runs the sign command on every file it ships: the app exe and the sidecar before bundling, the installer's NSIS plugin DLLs, the uninstaller (from inside makensis) and the finished installer. Every signature is SHA-256 and **timestamped** (SSL.com's timestamp server, with DigiCert's as fallback), so it stays valid after the certificate expires. Tauri makes the auto-updater's signatures *after* that, over the signed installer, so updates keep working.
4. **Every shipped Windows file is signed and timestamped** (`verify-windows-signatures.ps1`). It checks the installer, everything inside it (read with 7-Zip), and everything a silent install puts on disk, the uninstaller included. Any `.exe` or `.dll` that isn't validly signed and timestamped (or, with `WINDOWS_SIGNING_PUBLISHER`, signed by someone else) **fails the job**. The draft release already holds that build's files by then, so a failed check means: **do not publish the draft**. Fix the cause and build again.

The Windows bundle is NSIS only (`bundle.targets` in `src-tauri/tauri.conf.json`), so there is no MSI to sign. If an MSI target is ever added, Tauri signs it with the same command, and the check should then read it too.

The old SignPath job (`windows-sign`, `desktop/signpath/`) never runs while `WINDOWS_SIGNING` is set: one signer, one publisher.

## Check it on a real Windows PC

1. **Download the installer with a browser** from the GitHub release page (`Kato_<version>_x64-setup.exe`). Downloading it this way gives it the same "came from the internet" mark a user's copy has; copying it over from somewhere else does not.
2. **Right-click it → Properties → Digital Signatures.** You should see your name, with a timestamp. Select it → **Details**: "This digital signature is OK."
3. **Run it.** For the first releases SmartScreen may still say the app is unrecognized. The publisher line now shows your name instead of "Unknown publisher" (see the next section).
4. **After installing**, do the same check on the installed `Kato.exe`, `kato-sidecar.exe` and `uninstall.exe` (in `%LOCALAPPDATA%\Kato` unless you chose another folder).

## Reputation: what signing does not do on day one

A correctly signed installer can still show the SmartScreen warning for its first downloads. Microsoft builds reputation per file and per publisher over weeks and hundreds of clean installs. **EV certificates no longer skip this.** Microsoft removed that in 2024 ([SmartScreen reputation for app developers](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation)). There is no form that grants reputation. What helps, per Microsoft:

- sign **every** release, and every file in it (done here);
- keep **one** signing identity, and renew with the same name and subject: a new identity starts from zero;
- never modify a file after it is signed.

Until reputation builds, the warning names you as the publisher. That is the honest state of a new signed app, and it is what people should see.

## Defender false positives (the PyInstaller sidecar)

The sidecar is a PyInstaller build, and PyInstaller apps get flagged because malware is built with PyInstaller too. What this setup does about it:

- **The bootloader is built from source** on the Windows release runner. The prebuilt bootloader is byte-for-byte the same in every PyInstaller app, and PyInstaller's own docs recommend rebuilding it "to avoid anti-virus false positives" ([Building the Bootloader](https://pyinstaller.org/en/stable/bootloader-building.html)). The trade-off is a slower Windows build and a C compiler (the runner has MSVC).
- **UPX is off** (`upx=False` in `sidecar/kato-sidecar.spec`, already the case). UPX-packed executables are flagged far more often.
- **The sidecar is signed** with your certificate (Tauri signs `externalBin`).
- **One-file mode is kept, on purpose.** Tauri's `externalBin` launches one executable, and the macOS build, notarized, depends on it. One-dir mode would mean bundling a folder of files as resources, a different launch path, and signing every file in it on every OS. The cost of keeping one-file: at run time the sidecar unpacks its Python `.pyd`/`.dll` files to `%TEMP%`, and those are not signed, which Windows 11's Smart App Control may object to. If it does, the next step is signing those files inside the PyInstaller build, or one-dir.

### Reporting a false positive to Microsoft

When a signed release still gets flagged by Defender:

1. Go to **[microsoft.com/wdsi/filesubmission](https://www.microsoft.com/en-us/wdsi/filesubmission)** and sign in with a Microsoft account (you need one to follow the submission).
2. Choose **Software developer**.
3. Upload **the exact flagged file**, not a whole folder (up to 50 MB; the sidecar is about 30 MB). Fill in the product (Kato), your name as the company, and the detection name Defender showed.
4. Under *What do you believe this file is?*, choose **Incorrectly detected as malware/malicious**.
5. In the details (English, up to 1,900 characters), say: an open-source project (link the repository), built by GitHub Actions (link the run), a PyInstaller bundle, signed by you, downloaded from the GitHub release.
6. Follow it at [microsoft.com/wdsi/submissionhistory](https://www.microsoft.com/wdsi/submissionhistory). Every build changes the file, so a new release may need a new submission.

Do this yourself, by hand, with the file you downloaded. The build never uploads Kato's files to VirusTotal or anywhere else.

## Never

These get around Windows protection instead of earning trust, and Kato does none of them:

- stripping the Mark of the Web, or zip/archive tricks to avoid the warning;
- telling people to turn off SmartScreen or Defender;
- adding Defender exclusions from the installer.

The **Run anyway** step on an unsigned build is Windows' own choice for the person running it. The goal here is that they should not need it.
