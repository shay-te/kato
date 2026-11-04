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



class AddDoneStateTests(unittest.TestCase):
    """The board's "count it as done": a state added to the setting, saved."""

    def setUp(self) -> None:
        import json, tempfile
        from pathlib import Path
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / 'settings.json'
        self.read = lambda: json.loads(self.path.read_text())
        patcher = mock.patch.dict('os.environ', {'KATO_SETTINGS_FILE': str(self.path), DONE_STATES_KEY: ''})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_state_is_added_once_as_written(self) -> None:
        from kato_core_lib.helpers.done_states_utils import add_done_state
        self.assertEqual(add_done_state(' Waiting Deploy '), ['Waiting Deploy'])
        self.assertEqual(add_done_state('waiting deploy'), ['Waiting Deploy'])  # already there
        self.assertEqual(add_done_state('Released'), ['Waiting Deploy', 'Released'])
        self.assertEqual(self.read()[DONE_STATES_KEY], 'Waiting Deploy, Released')
        self.assertTrue(is_done_state('Released', 'Done'))  # at once, no restart

    def test_a_state_that_cannot_be_listed_is_refused(self) -> None:
        from kato_core_lib.helpers.done_states_utils import add_done_state
        for bad in ('', '   ', 'Done, Closed', 'x' * 81, None):
            with self.assertRaises(ValueError):
                add_done_state(bad)
        self.assertFalse(self.path.exists())  # nothing was written

    def test_states_no_workflow_word_names(self) -> None:
        from kato_core_lib.helpers.done_states_utils import add_done_state, unrecognized_states
        known = {'open', 'in progress', 'pull request', 'done'}
        states = ['Waiting Deploy', 'In Progress', 'waiting deploy', '', None, 'Pull Request', 'QA']
        self.assertEqual(unrecognized_states(states, known), ['Waiting Deploy', 'QA'])
        add_done_state('QA')
        self.assertEqual(unrecognized_states(states, known), ['Waiting Deploy'])


if __name__ == '__main__':
    unittest.main()
