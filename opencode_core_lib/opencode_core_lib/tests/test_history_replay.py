"""Replaying an OpenCode chat through the CLI's own session export."""

from __future__ import annotations

import json
import subprocess
import unittest
from unittest.mock import patch

from opencode_core_lib.opencode_core_lib.helpers.cli_generation import (
    GENERATION_V1,
    GENERATION_V2,
)
from opencode_core_lib.opencode_core_lib.session import history
from opencode_core_lib.opencode_core_lib.session.history import (
    events_from_export,
    export_arguments,
    load_history_events,
    opencode_binary,
    run_store_command,
)

_EXPORT = json.dumps({'info': {'id': 'ses_1'}, 'messages': [
    {'info': {'role': 'user', 'time': {'created': 1_763_000_000_000}},
     'parts': [{'type': 'text', 'text': 'fix the bug'}, {'type': 'text', 'text': 'x', 'synthetic': True}]},
    {'info': {'role': 'assistant'}, 'parts': [{'type': 'tool', 'tool': 'bash'},
                                             {'type': 'text', 'text': 'Fixed.'}]},
    {'info': {'role': 'system'}, 'parts': [{'type': 'text', 'text': 'hidden'}]},
    {'role': 'assistant', 'parts': 'odd'},
    'not a message',
]})


class ExportTests(unittest.TestCase):
    def test_user_and_assistant_text_replay_in_the_live_shape(self) -> None:
        events = events_from_export(_EXPORT)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]['message']['content'][0]['text'], 'fix the bug')
        self.assertTrue(events[0]['timestamp'].startswith('2025-'))
        self.assertEqual(events[1]['item'], {'type': 'agent_message', 'text': 'Fixed.'})
        self.assertNotIn('timestamp', events[1])

    def test_unreadable_exports_replay_nothing(self) -> None:
        for text in ('', 'not json', '[]', '{"messages": "x"}'):
            self.assertEqual(events_from_export(text), [])

    def test_the_export_command_per_generation(self) -> None:
        self.assertEqual(export_arguments('ses_1', GENERATION_V2), ['session', 'export', 'ses_1'])
        self.assertEqual(export_arguments('ses_1', GENERATION_V1), ['export', 'ses_1'])


class LoadHistoryTests(unittest.TestCase):
    def test_a_session_is_read_through_its_export_and_capped(self) -> None:
        with patch.object(history, 'cli_generation', return_value=GENERATION_V2), patch.object(
            history, 'run_store_command', return_value=_EXPORT,
        ) as run:
            self.assertEqual(len(load_history_events('ses_1', binary='opencode')), 2)
            self.assertEqual(len(load_history_events('ses_1', max_events=1)), 1)
            self.assertEqual(load_history_events('ses_1', max_events=0), [])
        self.assertEqual(run.call_args_list[0].args[0], ['session', 'export', 'ses_1'])

    def test_no_id_or_an_option_shaped_id_reads_nothing(self) -> None:
        with patch.object(history, 'run_store_command') as run:
            self.assertEqual(load_history_events(''), [])
            self.assertEqual(load_history_events('--help'), [])
        run.assert_not_called()


class StoreCommandTests(unittest.TestCase):
    def test_the_binary_comes_from_the_host_when_named(self) -> None:
        self.assertEqual(opencode_binary({'OPENCODE_CLI_BINARY': '/opt/oc'}), '/opt/oc')
        self.assertEqual(opencode_binary({}), 'opencode')

    def test_a_store_command_runs_standalone_and_answers_stdout_or_nothing(self) -> None:
        with patch.object(history, 'cli_generation', return_value=GENERATION_V2), patch(
            'opencode_core_lib.opencode_core_lib.session.history.subprocess.run',
            return_value=subprocess.CompletedProcess([], 0, 'out', ''),
        ) as run:
            self.assertEqual(run_store_command(['models'], binary='oc'), 'out')
        self.assertEqual(run.call_args.args[0], ['oc', 'models', '--standalone'])
        self.assertIs(run.call_args.kwargs['stdin'], subprocess.DEVNULL)
        for outcome in (subprocess.CompletedProcess([], 1, 'out', 'err'), OSError('missing')):
            kwargs = {'side_effect': outcome} if isinstance(outcome, Exception) else {'return_value': outcome}
            with self.subTest(outcome=outcome), patch.object(
                history, 'cli_generation', return_value=GENERATION_V1,
            ), patch('opencode_core_lib.opencode_core_lib.session.history.subprocess.run', **kwargs):
                self.assertEqual(run_store_command(['models'], binary='oc'), '')


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
