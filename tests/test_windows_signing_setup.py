"""Windows code signing: the pieces that must agree, pinned.

The scripts run only on GitHub's Windows runner (desktop/signing/), so what can
drift unnoticed is checked here: the secret names the workflow passes, the
script requires and the README tells the operator to create; that signing is
reachable only on Windows and only when switched on; that the verification runs
whenever signing did; that local builds can never sign; and that the old
SignPath job cannot run beside it.
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github' / 'workflows' / 'desktop-release.yaml'
SIGNING = ROOT / 'desktop' / 'signing'
SECRETS = ('ESIGNER_USERNAME', 'ESIGNER_PASSWORD', 'ESIGNER_CREDENTIAL_ID', 'ESIGNER_TOTP_SECRET')


class WindowsSigningSetupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = yaml.safe_load(WORKFLOW.read_text(encoding='utf-8'))
        cls.steps = cls.workflow['jobs']['build']['steps']

    def step(self, name: str) -> dict:
        return next(step for step in self.steps if step.get('name') == name)

    def test_the_secret_names_agree_everywhere(self) -> None:
        enable = (SIGNING / 'enable-windows-signing.ps1').read_text(encoding='utf-8')
        sign = (SIGNING / 'sign-windows.ps1').read_text(encoding='utf-8')
        readme = (SIGNING / 'README.md').read_text(encoding='utf-8')
        switch = self.step('Windows code signing, when configured')['env']
        build_env = self.step('Build + sign the Tauri app (emits installers + updater feed)')['env']
        for name in SECRETS:
            with self.subTest(secret=name):
                self.assertEqual(switch[name], '${{ secrets.%s }}' % name)
                self.assertIn(f"'{name}'", enable)
                self.assertIn(f'$env:{name}', sign)
                self.assertIn(f'| Secret | `{name}` |', readme)
                # The sign command reads them inside tauri build: on Windows only.
                self.assertEqual(build_env[name], "${{ matrix.os == 'windows-latest' && secrets.%s || '' }}" % name)
        self.assertEqual(switch['WINDOWS_SIGNING'], '${{ vars.WINDOWS_SIGNING }}')
        self.assertIn('| Variable | `WINDOWS_SIGNING` | `esigner` |', readme)

    def test_signing_is_windows_only_and_switched_on_by_the_variable(self) -> None:
        switch = self.step('Windows code signing, when configured')
        self.assertEqual(switch['if'], "matrix.os == 'windows-latest'")
        self.assertEqual(switch['id'], 'winsign')
        self.assertEqual(switch['run'], './desktop/signing/enable-windows-signing.ps1')
        java = self.step('Java for Windows code signing')
        self.assertEqual(java['if'], "matrix.os == 'windows-latest' && vars.WINDOWS_SIGNING != ''")
        build = self.step('Build + sign the Tauri app (emits installers + updater feed)')
        self.assertEqual(build['with']['args'], '${{ steps.winsign.outputs.tauri_args }}')
        # Java and the switch come before the build that signs.
        names = [step.get('name') for step in self.steps]
        self.assertLess(names.index('Java for Windows code signing'), names.index(switch['name']))
        self.assertLess(names.index(switch['name']), names.index(build['name']))

    def test_whenever_it_signed_the_shipped_files_are_checked(self) -> None:
        verify = self.step('Every shipped Windows file is signed and timestamped')
        self.assertEqual(verify['if'], "matrix.os == 'windows-latest' && steps.winsign.outputs.signed == 'true'")
        self.assertIn('verify-windows-signatures.ps1', verify['run'])
        names = [step.get('name') for step in self.steps]
        self.assertGreater(names.index(verify['name']),
                           names.index('Build + sign the Tauri app (emits installers + updater feed)'))
        script = (SIGNING / 'verify-windows-signatures.ps1').read_text(encoding='utf-8')
        for must in ('Get-AuthenticodeSignature', 'TimeStamperCertificate', "'/S'", 'uninstall*.exe', 'kato-sidecar*.exe'):
            self.assertIn(must, script)

    def test_the_switch_step_says_both_outputs_on_every_path(self) -> None:
        enable = (SIGNING / 'enable-windows-signing.ps1').read_text(encoding='utf-8')
        self.assertEqual(len(re.findall(r"Write-StepOutput 'signed'", enable)), 2)
        self.assertEqual(len(re.findall(r"Write-StepOutput 'tauri_args'", enable)), 2)

    def test_jsign_is_pinned_by_version_and_checksum(self) -> None:
        enable = (SIGNING / 'enable-windows-signing.ps1').read_text(encoding='utf-8')
        self.assertRegex(enable, r"\$JsignVersion = '\d+\.\d+'")
        self.assertRegex(enable, r"\$JsignSha256 = '[0-9a-f]{64}'")
        self.assertIn('Get-FileHash', enable)

    def test_every_signature_is_timestamped_and_sha256(self) -> None:
        sign = (SIGNING / 'sign-windows.ps1').read_text(encoding='utf-8')
        for must in ("'--tsaurl'", "'--tsmode', 'RFC3161'", "'--alg', 'SHA-256'", "'--replace'"):
            self.assertIn(must, sign)

    def test_local_builds_can_never_sign(self) -> None:
        for name in ('tauri.conf.json', 'tauri.windows.conf.json'):
            config = json.loads((ROOT / 'desktop' / 'src-tauri' / name).read_text(encoding='utf-8'))
            windows = config.get('bundle', {}).get('windows', {})
            self.assertNotIn('signCommand', windows, name)
            self.assertNotIn('certificateThumbprint', windows, name)

    def test_the_old_signpath_job_never_runs_beside_it(self) -> None:
        self.assertIn("vars.WINDOWS_SIGNING == ''", self.workflow['jobs']['windows-sign']['if'])
        keep = self.step('Keep the unsigned installer for signing')
        self.assertIn("vars.WINDOWS_SIGNING == ''", keep['if'])

    def test_the_pyinstaller_bootloader_is_built_from_source_on_windows_only(self) -> None:
        install = self.step('Install kato + PyInstaller')['run']
        self.assertIn('PYINSTALLER_COMPILE_BOOTLOADER=1 python -m pip install --no-binary pyinstaller pyinstaller', install)
        self.assertIn('if [ "${{ matrix.os }}" = "windows-latest" ]', install)
        linux = self.workflow['jobs']['linux-sidecar']['steps']
        linux_install = next(step for step in linux if step.get('name') == 'Install kato + PyInstaller')['run']
        self.assertNotIn('--no-binary', linux_install)  # macOS / Linux keep the wheel

    def test_no_certificate_or_key_is_tracked(self) -> None:
        import subprocess
        if not (ROOT / '.git').exists():
            self.skipTest('not a git checkout (e.g. a `git archive` copy)')
        tracked = subprocess.run(['git', 'ls-files'], cwd=ROOT, capture_output=True, text=True, check=True).stdout
        for line in tracked.splitlines():
            with self.subTest(file=line):
                self.assertNotRegex(line.lower(), r'\.(p12|pfx|pem|key|cer)$')


if __name__ == '__main__':
    unittest.main()
