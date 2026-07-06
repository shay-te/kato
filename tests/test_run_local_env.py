"""Precedence of the `kato up` process env composition (scripts/_script_utils).

Regression: `kato up` used to load `.env` OVER the shell and never read
~/.kato/settings.json, so a value saved through the Settings UI (which writes
settings.json) had no effect. The launcher now composes
`shell > settings.json > .env`.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
import _script_utils  # noqa: E402
from _script_utils import layered_env, read_kato_settings_file  # noqa: E402


class LayeredEnvTests(unittest.TestCase):
    def test_precedence_shell_over_settings_over_dotenv(self) -> None:
        base = {'A': 'shell'}                       # real shell env
        settings = {'A': 'settings', 'B': 'settings'}
        dotenv = {'A': 'dotenv', 'B': 'dotenv', 'C': 'dotenv'}
        env = layered_env(base, settings, dotenv)
        self.assertEqual(env['A'], 'shell')     # shell wins over both
        self.assertEqual(env['B'], 'settings')  # settings.json beats .env
        self.assertEqual(env['C'], 'dotenv')    # .env fills what's left

    def test_does_not_mutate_base_and_coerces_str(self) -> None:
        base = {'X': 'x'}
        env = layered_env(base, {'N': 1}, None, {'B': True})
        self.assertEqual(base, {'X': 'x'})       # base untouched
        self.assertEqual(env['N'], '1')          # values coerced to str
        self.assertEqual(env['B'], 'True')


class ReadKatoSettingsFileTests(unittest.TestCase):
    def test_reads_flat_json_via_env_override(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'settings.json'
            path.write_text(json.dumps({'BITBUCKET_API_TOKEN': 'tok', 'N': 7}))
            with mock.patch.dict(os.environ, {'KATO_SETTINGS_FILE': str(path)}):
                got = read_kato_settings_file()
        self.assertEqual(got, {'BITBUCKET_API_TOKEN': 'tok', 'N': '7'})

    def test_missing_or_corrupt_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            missing = Path(d) / 'nope.json'
            with mock.patch.dict(os.environ, {'KATO_SETTINGS_FILE': str(missing)}):
                self.assertEqual(read_kato_settings_file(), {})
            corrupt = Path(d) / 'bad.json'
            corrupt.write_text('{ not json')
            with mock.patch.dict(os.environ, {'KATO_SETTINGS_FILE': str(corrupt)}):
                self.assertEqual(read_kato_settings_file(), {})
            arr = Path(d) / 'arr.json'
            arr.write_text('[1,2,3]')  # not an object
            with mock.patch.dict(os.environ, {'KATO_SETTINGS_FILE': str(arr)}):
                self.assertEqual(read_kato_settings_file(), {})

    def test_matches_canonical_reader_contract(self) -> None:
        # The stdlib mirror must agree with kato_core_lib's canonical reader
        # (same path override, same tolerant/str-coerce contract).
        from kato_core_lib.helpers.kato_settings_store_utils import read_kato_settings
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'settings.json'
            path.write_text(json.dumps({'K': 'v', 'EMPTY': '', 'NUM': 3}))
            with mock.patch.dict(os.environ, {'KATO_SETTINGS_FILE': str(path)}):
                self.assertEqual(read_kato_settings_file(), read_kato_settings())


if __name__ == '__main__':
    unittest.main()


class RunLocalAutoBootstrapTests(unittest.TestCase):
    """``kato up`` on a fresh clone bootstraps itself — the operator's
    onboarding is ONE command, ending in the browser wizard."""

    def _run_main(self, venv_exists_sequence, bootstrap_rc=0):
        import run_local
        from unittest import mock
        fake_python = mock.Mock()
        fake_python.exists.side_effect = venv_exists_sequence
        fake_python.__str__ = lambda self: '/fake/.venv/bin/python'
        calls = {}

        def fake_call(cmd, cwd=None):
            calls['bootstrap_cmd'] = cmd
            return bootstrap_rc

        completed = mock.Mock(returncode=0)
        with mock.patch.object(run_local, 'venv_python_path', return_value=fake_python), \
             mock.patch.object(run_local.subprocess, 'call', side_effect=fake_call), \
             mock.patch.object(run_local.subprocess, 'run', return_value=completed) as run_mock:
            rc = run_local.main()
        return rc, calls, run_mock

    def test_existing_venv_skips_bootstrap(self) -> None:
        rc, calls, run_mock = self._run_main(venv_exists_sequence=[True])
        self.assertEqual(rc, 0)
        self.assertNotIn('bootstrap_cmd', calls)
        run_mock.assert_called_once()

    def test_missing_venv_bootstraps_then_starts_kato(self) -> None:
        # exists(): False (first check) → True (post-bootstrap re-check)
        rc, calls, run_mock = self._run_main(venv_exists_sequence=[False, True])
        self.assertEqual(rc, 0)
        self.assertIn('--skip-tests', calls['bootstrap_cmd'])
        self.assertTrue(str(calls['bootstrap_cmd'][1]).endswith('bootstrap.py'))
        run_mock.assert_called_once()

    def test_failed_bootstrap_stops_before_starting_kato(self) -> None:
        rc, calls, run_mock = self._run_main(
            venv_exists_sequence=[False], bootstrap_rc=3,
        )
        self.assertEqual(rc, 3)
        run_mock.assert_not_called()
