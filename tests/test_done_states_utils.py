"""Ticket states that mean done, beside the provider's own done state.

The operator lists them (``KATO_DONE_STATES``, comma-separated); the setting is
read fresh, so a save applies at once. Case and stray spaces do not matter.
"""

from __future__ import annotations

import unittest
from unittest import mock

from kato_core_lib.helpers.done_states_utils import DONE_STATES_KEY, extra_done_states, is_done_state


class DoneStatesTests(unittest.TestCase):
    def _with(self, value: str):
        return mock.patch.dict('os.environ', {DONE_STATES_KEY: value})

    def test_the_listed_states_lower_cased_and_trimmed(self) -> None:
        with self._with(' Waiting Deploy ,Released,, '):
            self.assertEqual(extra_done_states(), frozenset({'waiting deploy', 'released'}))

    def test_nothing_listed(self) -> None:
        with self._with(''):
            self.assertEqual(extra_done_states(), frozenset())

    def test_the_provider_s_done_state_or_a_listed_one_is_done(self) -> None:
        with self._with('Waiting Deploy'):
            self.assertTrue(is_done_state('Done', 'Done'))
            self.assertTrue(is_done_state('  done ', 'Done'))
            self.assertTrue(is_done_state('WAITING DEPLOY', 'Done'))
            self.assertFalse(is_done_state('In Progress', 'Done'))

    def test_no_state_is_never_done(self) -> None:
        with self._with('Waiting Deploy'):
            self.assertFalse(is_done_state('', 'Done'))
            self.assertFalse(is_done_state('', ''))
            self.assertFalse(is_done_state(None, 'Done'))

    def test_no_provider_done_state_leaves_only_the_listed_ones(self) -> None:
        with self._with('Waiting Deploy'):
            self.assertTrue(is_done_state('Waiting Deploy', ''))
            self.assertFalse(is_done_state('Done', ''))

    def test_the_setting_is_offered_in_settings_and_applies_without_a_restart(self) -> None:
        from kato_core_lib.helpers.kato_settings_schema_utils import SETTINGS_SCHEMA
        from kato_webserver.app import _apply_live_settings, _live_settings_keys

        keys = [field[0] for section in SETTINGS_SCHEMA for field in section['fields']]
        self.assertIn(DONE_STATES_KEY, keys)
        self.assertIn(DONE_STATES_KEY, _live_settings_keys())
        app = mock.MagicMock()
        app.config = {}
        self.assertIn('within a minute', _apply_live_settings(app, {DONE_STATES_KEY: 'Waiting Deploy'}))


if __name__ == '__main__':
    unittest.main()
