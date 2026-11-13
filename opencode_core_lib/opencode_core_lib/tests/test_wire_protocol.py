"""OpenCode's ``--format json`` lines, translated, and the argv/env every run builds."""

from __future__ import annotations

import json
import unittest

from agent_core_lib.agent_core_lib.helpers.command_floor import (
    GIT_ACCESS_LOCAL,
    GIT_ACCESS_STRICT,
)
from agent_core_lib.agent_core_lib.session.turn_events import (
    TURN_EVENT_SESSION_STARTED,
    TURN_EVENT_TURN_FAILED,
    TURN_ITEM_AGENT_MESSAGE,
    TURN_ITEM_COMMAND_EXECUTION,
    TURN_ITEM_FILE_CHANGE,
    TURN_ITEM_MCP_TOOL_CALL,
    TURN_ITEM_REASONING,
    TURN_ITEM_WEB_SEARCH,
)
from opencode_core_lib.opencode_core_lib.helpers.cli_generation import (
    GENERATION_V1,
    GENERATION_V2,
)
from opencode_core_lib.opencode_core_lib.session.wire_protocol import (
    CONFIG_ENV_KEY,
    MODE_READ_ONLY,
    error_message,
    model_arguments,
    permission_config,
    run_arguments,
    run_environment,
    translate_event,
)


def _tool(tool: str, **state) -> dict:
    return {'type': 'tool_use', 'part': {'tool': tool, 'state': state}}


class TranslateTests(unittest.TestCase):
    def test_a_step_start_names_the_session(self) -> None:
        self.assertEqual(
            translate_event({'type': 'step_start', 'sessionID': 'ses_1', 'part': {}}),
            [{'type': TURN_EVENT_SESSION_STARTED, 'thread_id': 'ses_1'}],
        )
        self.assertEqual(
            translate_event({'type': 'step_start', 'part': {'sessionID': 'ses_2'}})[0]['thread_id'],
            'ses_2',
        )
        self.assertEqual(translate_event({'type': 'step_start', 'sessionID': ''}), [])

    def test_text_and_reasoning_become_items(self) -> None:
        text = translate_event({'type': 'text', 'part': {'type': 'text', 'text': 'Done.'}})
        thought = translate_event({'type': 'reasoning', 'part': {'text': 'hmm'}})
        self.assertEqual(text[0]['item'], {'type': TURN_ITEM_AGENT_MESSAGE, 'text': 'Done.'})
        self.assertEqual(thought[0]['item']['type'], TURN_ITEM_REASONING)
        self.assertEqual(translate_event({'type': 'text', 'part': {'text': '  '}}), [])

    def test_tools_become_the_items_the_chat_draws(self) -> None:
        cases = (
            (_tool('bash', input={'command': 'pytest'}, output='ok'), TURN_ITEM_COMMAND_EXECUTION),
            (_tool('edit', input={'filePath': 'a.py'}), TURN_ITEM_FILE_CHANGE),
            (_tool('write', input={'file_path': 'b.py'}), TURN_ITEM_FILE_CHANGE),
            (_tool('websearch', input={'query': 'q'}), TURN_ITEM_WEB_SEARCH),
            (_tool('webfetch', input={'url': 'https://x'}), TURN_ITEM_WEB_SEARCH),
            (_tool('glob', input={'pattern': '*'}), TURN_ITEM_MCP_TOOL_CALL),
            ({'type': 'tool_use', 'part': {'tool': 'edit', 'state': 'odd'}}, TURN_ITEM_MCP_TOOL_CALL),
        )
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(translate_event(raw)[0]['item']['type'], expected)
        bash = translate_event(_tool('bash', input={'command': 'ls'}, output='a\nb'))[0]['item']
        self.assertEqual(bash['aggregated_output'], 'a\nb')

    def test_an_error_line_fails_the_turn_in_either_generation_s_shape(self) -> None:
        v2 = translate_event({'type': 'error', 'error': {'type': 'auth', 'message': 'No key'}})
        v1 = translate_event({'type': 'error', 'error': {'name': 'ProviderAuthError', 'data': {'message': 'No key v1'}}})
        self.assertEqual(v2[0]['type'], TURN_EVENT_TURN_FAILED)
        self.assertEqual(v2[0]['error']['message'], 'No key')
        self.assertEqual(v1[0]['error']['message'], 'No key v1')

    def test_quiet_lines_say_nothing(self) -> None:
        for kind in ('step_finish', 'retry', 'background_wait', 'unknown'):
            self.assertEqual(translate_event({'type': kind, 'part': {}}), [])

    def test_error_message_reads_every_shape(self) -> None:
        self.assertEqual(error_message('plain'), 'plain')
        self.assertEqual(error_message(None), 'the turn failed')
        self.assertEqual(error_message({'name': 'MessageAbortedError'}), 'MessageAbortedError')
        self.assertEqual(error_message({'type': 'unknown'}), 'unknown')
        self.assertEqual(error_message({}), 'the turn failed')


class ArgvTests(unittest.TestCase):
    def test_v2_runs_standalone_and_v1_does_not(self) -> None:
        self.assertEqual(run_arguments(['opencode'], GENERATION_V2),
                         ['opencode', 'run', '--format', 'json', '--standalone'])
        self.assertEqual(run_arguments(['opencode'], GENERATION_V1),
                         ['opencode', 'run', '--format', 'json'])

    def test_the_effort_is_a_variant_spelled_per_generation(self) -> None:
        self.assertEqual(model_arguments('anthropic/x', 'high', GENERATION_V2), ['-m', 'anthropic/x#high'])
        self.assertEqual(model_arguments('anthropic/x', '', GENERATION_V2), ['-m', 'anthropic/x'])
        self.assertEqual(model_arguments('', 'high', GENERATION_V2), [])
        self.assertEqual(model_arguments('anthropic/x', 'high', GENERATION_V1),
                         ['-m', 'anthropic/x', '--variant', 'high'])
        self.assertEqual(model_arguments('', 'low', GENERATION_V1), ['--variant', 'low'])
        self.assertEqual(model_arguments('openai/gpt-5', '', GENERATION_V1), ['-m', 'openai/gpt-5'])


class PermissionTests(unittest.TestCase):
    def test_the_floor_is_denied_after_the_catch_all(self) -> None:
        shell = permission_config(GIT_ACCESS_STRICT)['permission']['bash']
        self.assertEqual(next(iter(shell)), '*')  # the last matching rule wins
        self.assertEqual(shell['*'], 'allow')
        self.assertEqual(shell['git push'], 'deny')
        self.assertEqual(shell['git push *'], 'deny')
        self.assertEqual(shell['git commit *'], 'deny')

    def test_local_git_access_releases_commit_but_not_push(self) -> None:
        shell = permission_config(GIT_ACCESS_LOCAL)['permission']['bash']
        self.assertNotIn('git commit *', shell)
        self.assertEqual(shell['git push *'], 'deny')

    def test_nothing_outside_the_project_and_read_only_reads_only(self) -> None:
        self.assertEqual(permission_config()['permission']['external_directory'], 'deny')
        read_only = permission_config(mode=MODE_READ_ONLY)['permission']
        self.assertEqual((read_only['edit'], read_only['bash'], read_only['webfetch']),
                         ('deny', 'deny', 'deny'))

    def test_the_environment_carries_the_rules_and_the_project(self) -> None:
        env = run_environment('/work/task', git_access=GIT_ACCESS_LOCAL)
        self.assertEqual(env['PWD'], '/work/task')
        self.assertEqual(env['OPENCODE_DISABLE_AUTOUPDATE'], '1')
        self.assertEqual(json.loads(env[CONFIG_ENV_KEY]), permission_config(GIT_ACCESS_LOCAL))
        self.assertNotIn('PWD', run_environment(''))


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
