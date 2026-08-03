"""Every ``~/.kato`` path resolves the same way — pinned.

Four call sites had grown their own resolver and three dropped a step. Neither
omission raises; both fail quietly in a way that looks like configuration:

* ``KATO_HOOKS_CONFIG=~/hooks.json`` was passed to a raw ``Path(...)``, so it
  became a RELATIVE directory literally named ``~``. That path does not exist,
  the loader read it as "no hooks configured", and kato booted with the hook
  chain off — a fail-open on a control the boot path fails closed on.
* ``KATO_SESSION_STATE_DIR=' '`` was truthy in the webserver copy (no strip),
  so session metadata went to a directory named after a space while the
  orchestrator used ``~/.kato/sessions``. The UI then showed no chats.

These tests fail against either old copy.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kato_core_lib.helpers.kato_paths_utils import (
    SESSION_STATE_DIR_ENV_KEY,
    configured_path,
    kato_home_path,
    kato_session_state_dir,
)


class ConfiguredPathTests(unittest.TestCase):
    def test_blank_and_whitespace_are_unset(self) -> None:
        # A wrapper script exporting the variable empty must fall through to
        # the default, not resolve to '' / ' '.
        for blank in ('', '   ', '\t\n', None):
            self.assertIsNone(configured_path(blank), repr(blank))

    def test_tilde_is_expanded(self) -> None:
        self.assertEqual(configured_path('~/x.json'), Path.home() / 'x.json')

    def test_surrounding_whitespace_is_trimmed(self) -> None:
        self.assertEqual(configured_path('  /tmp/x.json  '), Path('/tmp/x.json'))

    def test_absolute_path_passes_through(self) -> None:
        self.assertEqual(configured_path('/var/state/x'), Path('/var/state/x'))


class KatoHomePathTests(unittest.TestCase):
    def test_defaults_under_kato_home(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                kato_home_path('thing.json', env_key='KATO_THING_PATH'),
                Path.home() / '.kato' / 'thing.json',
            )

    def test_env_override_wins_and_expands_tilde(self) -> None:
        with patch.dict(os.environ, {'KATO_THING_PATH': '~/elsewhere/thing.json'}):
            self.assertEqual(
                kato_home_path('thing.json', env_key='KATO_THING_PATH'),
                Path.home() / 'elsewhere' / 'thing.json',
            )

    def test_blank_env_override_falls_back_to_the_default(self) -> None:
        with patch.dict(os.environ, {'KATO_THING_PATH': '   '}):
            self.assertEqual(
                kato_home_path('thing.json', env_key='KATO_THING_PATH'),
                Path.home() / '.kato' / 'thing.json',
            )


class SessionStateDirTests(unittest.TestCase):
    """The orchestrator and the webserver must resolve this identically."""

    def test_defaults_to_kato_sessions(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                kato_session_state_dir(),
                str(Path.home() / '.kato' / 'sessions'),
            )

    def test_env_override_expands_tilde(self) -> None:
        with patch.dict(os.environ, {SESSION_STATE_DIR_ENV_KEY: '~/sess'}):
            self.assertEqual(kato_session_state_dir(), str(Path.home() / 'sess'))

    def test_whitespace_only_env_is_not_a_directory_name(self) -> None:
        # The webserver copy took ' ' as truthy and created a directory named
        # after a space, splitting session metadata from the orchestrator's.
        with patch.dict(os.environ, {SESSION_STATE_DIR_ENV_KEY: '   '}):
            self.assertEqual(
                kato_session_state_dir(),
                str(Path.home() / '.kato' / 'sessions'),
            )

    def test_explicit_argument_outranks_the_environment(self) -> None:
        with patch.dict(os.environ, {SESSION_STATE_DIR_ENV_KEY: '/from/env'}):
            self.assertEqual(kato_session_state_dir('/explicit'), '/explicit')

    def test_blank_explicit_argument_falls_through_to_the_environment(self) -> None:
        with patch.dict(os.environ, {SESSION_STATE_DIR_ENV_KEY: '/from/env'}):
            self.assertEqual(kato_session_state_dir('  '), '/from/env')


class HooksConfigPathTests(unittest.TestCase):
    """``KATO_HOOKS_CONFIG`` must survive a ``~``."""

    def test_tilde_in_the_env_var_resolves_to_the_real_file(self) -> None:
        from kato_core_lib.hooks.config import _resolve_path

        with tempfile.TemporaryDirectory() as home:
            hooks_file = Path(home) / 'my-hooks.json'
            hooks_file.write_text('{}')
            # Point ``~`` at the temp dir so '~/my-hooks.json' is a real file.
            with patch.dict(os.environ, {
                'HOME': home,
                'USERPROFILE': home,
                'KATO_HOOKS_CONFIG': '~/my-hooks.json',
            }):
                resolved = _resolve_path(None)
        self.assertIsNotNone(
            resolved,
            'a ~-prefixed KATO_HOOKS_CONFIG read as "no hooks configured" — '
            'the hook chain silently disabled at boot',
        )
        self.assertEqual(resolved, hooks_file)

    def test_tilde_in_the_explicit_argument_also_resolves(self) -> None:
        from kato_core_lib.hooks.config import _resolve_path

        with tempfile.TemporaryDirectory() as home:
            hooks_file = Path(home) / 'explicit-hooks.json'
            hooks_file.write_text('{}')
            with patch.dict(os.environ, {'HOME': home, 'USERPROFILE': home}):
                resolved = _resolve_path('~/explicit-hooks.json')
        self.assertEqual(resolved, hooks_file)

    def test_a_configured_path_that_is_missing_stays_none(self) -> None:
        # Must NOT fall back to ~/.kato/hooks.json: an operator who names a
        # hooks file should never silently get a different one.
        from kato_core_lib.hooks.config import _resolve_path

        with patch.dict(os.environ, {'KATO_HOOKS_CONFIG': '/no/such/hooks.json'}):
            self.assertIsNone(_resolve_path(None))


if __name__ == '__main__':
    unittest.main()
