"""Tests for ``kato_core_lib.helpers.dotenv_utils.load_dotenv_into_environ``.

Pinned behaviour mirrors what the dispatcher + the approve-repo
script's belt-and-suspenders loader rely on. If either consumer
reads from a slightly different parser the next time, the diff
shows up here first.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kato_core_lib.helpers.dotenv_utils import load_dotenv_into_environ


class LoadDotenvIntoEnvironTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = Path(self._tmp.name)
        # Snapshot env so each test runs in isolation regardless of
        # what the previous test inserted.
        self._env_patch = patch.dict(os.environ, {}, clear=False)
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)

    def _write(self, body: str) -> Path:
        path = self.tmpdir / '.env'
        path.write_text(body, encoding='utf-8')
        return path

    def test_missing_file_returns_zero_and_does_not_raise(self) -> None:
        self.assertEqual(
            load_dotenv_into_environ(self.tmpdir / 'no-such.env'),
            0,
        )

    def test_loads_simple_keys(self) -> None:
        env = self._write('FOO=bar\nBAZ=qux\n')
        added = load_dotenv_into_environ(env)
        self.assertEqual(added, 2)
        self.assertEqual(os.environ.get('FOO'), 'bar')
        self.assertEqual(os.environ.get('BAZ'), 'qux')

    def test_real_environment_wins_over_dotenv(self) -> None:
        os.environ['SHELL_WINS'] = 'shell-value'
        env = self._write('SHELL_WINS=dotenv-value\nNEW_KEY=new\n')
        added = load_dotenv_into_environ(env)
        # Only ``NEW_KEY`` was actually added; ``SHELL_WINS`` was
        # left alone because the shell already exported it. This is
        # the load-bearing rule that protects operator overrides.
        self.assertEqual(added, 1)
        self.assertEqual(os.environ.get('SHELL_WINS'), 'shell-value')
        self.assertEqual(os.environ.get('NEW_KEY'), 'new')

    def test_skips_comments_and_blank_lines(self) -> None:
        env = self._write('# header comment\n\nFOO=bar\n   \n# trailing\n')
        self.assertEqual(load_dotenv_into_environ(env), 1)
        self.assertEqual(os.environ.get('FOO'), 'bar')

    def test_strips_export_prefix_for_bash_compat(self) -> None:
        env = self._write('export FOO=bar\nexport   BAZ=qux\n')
        load_dotenv_into_environ(env)
        self.assertEqual(os.environ.get('FOO'), 'bar')
        self.assertEqual(os.environ.get('BAZ'), 'qux')

    def test_strips_matched_surrounding_quotes(self) -> None:
        env = self._write(
            'SINGLE=\'value-1\'\n'
            'DOUBLE="value-2"\n'
            'BARE=value-3\n'
        )
        load_dotenv_into_environ(env)
        self.assertEqual(os.environ.get('SINGLE'), 'value-1')
        self.assertEqual(os.environ.get('DOUBLE'), 'value-2')
        self.assertEqual(os.environ.get('BARE'), 'value-3')

    def test_preserves_embedded_quotes(self) -> None:
        # Mismatched / embedded quotes must NOT get stripped — only
        # a single matched pair surrounding the whole value does.
        env = self._write('TOKEN="ab\'cd"\nWEIRD=\'has"inner\'\n')
        load_dotenv_into_environ(env)
        self.assertEqual(os.environ.get('TOKEN'), "ab'cd")
        self.assertEqual(os.environ.get('WEIRD'), 'has"inner')

    def test_skips_malformed_lines_silently(self) -> None:
        # Malformed line (no ``=``) is dropped; the surrounding good
        # lines still load. This is what "best-effort parser" means
        # — we never want a stray line to block bootstrap.
        env = self._write('OK=yes\nthis-line-has-no-equals\nALSO=fine\n')
        added = load_dotenv_into_environ(env)
        self.assertEqual(added, 2)
        self.assertEqual(os.environ.get('OK'), 'yes')
        self.assertEqual(os.environ.get('ALSO'), 'fine')

    def test_blank_key_is_dropped(self) -> None:
        env = self._write('=lonely-value\nFOO=ok\n')
        added = load_dotenv_into_environ(env)
        self.assertEqual(added, 1)
        self.assertEqual(os.environ.get('FOO'), 'ok')


if __name__ == '__main__':
    unittest.main()
