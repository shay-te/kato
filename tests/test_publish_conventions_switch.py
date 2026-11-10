"""Coverage for ``KATO_PUBLISH_FOLLOWS_PROJECT_CONVENTIONS`` — the gate that
decides whether kato's commit message / PR title / PR description follow a
repository's own CLAUDE.md / AGENTS.md.

Off by default, same reasoning as ``KATO_AUTO_PUSH_ENABLED``
(``test_auto_push_switch.py``): this changes text that lands in a shared
repository's history and pull requests, so an operator who never touches the
switch gets kato's own plain text, not the model-reshaped one.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kato_core_lib.helpers.kato_settings_schema_utils import (
    DEFAULT_ON_BOOL_KEYS,
    all_settings_keys,
)
from kato_core_lib.helpers.publish_conventions_gate_utils import (
    PUBLISH_FOLLOWS_PROJECT_CONVENTIONS_KEY,
    publish_follows_project_conventions,
)


class _SettingsFileBase(unittest.TestCase):
    """Points ``kato_settings_path()`` at a per-test tmpfile."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.settings_path = Path(self._tmp.name) / 'settings.json'
        patcher = patch.dict(
            os.environ, {'KATO_SETTINGS_FILE': str(self.settings_path)},
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop(PUBLISH_FOLLOWS_PROJECT_CONVENTIONS_KEY, None)

    def _write_settings(self, value: str) -> None:
        self.settings_path.write_text(
            json.dumps({PUBLISH_FOLLOWS_PROJECT_CONVENTIONS_KEY: value}), encoding='utf-8',
        )


class PublishConventionsGateResolverTests(_SettingsFileBase):

    def test_unset_means_disabled(self) -> None:
        self.assertFalse(publish_follows_project_conventions(env={}))

    def test_settings_file_true_enables(self) -> None:
        self._write_settings('true')
        self.assertTrue(publish_follows_project_conventions(env={}))

    def test_settings_file_false_disables(self) -> None:
        self._write_settings('false')
        self.assertFalse(publish_follows_project_conventions(env={}))

    def test_alternative_truthy_spellings(self) -> None:
        for spelling in ('true', 'TRUE', ' True ', '1', 'yes', 'on'):
            self._write_settings(spelling)
            self.assertTrue(
                publish_follows_project_conventions(env={}),
                f'{spelling!r} should read as on',
            )

    def test_anything_unrecognised_stays_off(self) -> None:
        for spelling in ('maybe', 'yes please', '2', ''):
            self._write_settings(spelling)
            self.assertFalse(
                publish_follows_project_conventions(env={}),
                f'{spelling!r} should read as off',
            )

    def test_settings_file_beats_shell_env(self) -> None:
        self._write_settings('false')
        self.assertFalse(
            publish_follows_project_conventions(
                env={PUBLISH_FOLLOWS_PROJECT_CONVENTIONS_KEY: 'true'},
            ),
        )

    def test_shell_env_used_when_settings_file_silent(self) -> None:
        self.assertTrue(
            publish_follows_project_conventions(
                env={PUBLISH_FOLLOWS_PROJECT_CONVENTIONS_KEY: 'true'},
            ),
        )

    def test_corrupt_settings_file_falls_through_to_default(self) -> None:
        self.settings_path.write_text('{not json', encoding='utf-8')
        self.assertFalse(publish_follows_project_conventions(env={}))

    def test_key_is_offered_in_the_settings_ui(self) -> None:
        self.assertIn(PUBLISH_FOLLOWS_PROJECT_CONVENTIONS_KEY, all_settings_keys())

    def test_key_is_not_registered_as_default_on(self) -> None:
        # It defaults OFF, so the UI toggle must draw unchecked.
        self.assertNotIn(PUBLISH_FOLLOWS_PROJECT_CONVENTIONS_KEY, DEFAULT_ON_BOOL_KEYS)


if __name__ == '__main__':
    unittest.main()
