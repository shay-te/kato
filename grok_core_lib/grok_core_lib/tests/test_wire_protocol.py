"""Grok's ``streaming-messages-json`` lines, translated, and the argv every run builds."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from agent_core_lib.agent_core_lib.helpers.command_floor import (
    GIT_ACCESS_LOCAL,
    GIT_ACCESS_STRICT,
)
from agent_core_lib.agent_core_lib.session.turn_events import (
    TURN_EVENT_ITEM_COMPLETED,
    TURN_EVENT_SESSION_STARTED,
    TURN_EVENT_TURN_COMPLETED,
    TURN_EVENT_TURN_FAILED,
    TURN_ITEM_AGENT_MESSAGE,
    TURN_ITEM_COMMAND_EXECUTION,
    TURN_ITEM_FILE_CHANGE,
    TURN_ITEM_MCP_TOOL_CALL,
    TURN_ITEM_REASONING,
    TURN_ITEM_WEB_SEARCH,
)
from grok_core_lib.grok_core_lib.session import wire_protocol
from grok_core_lib.grok_core_lib.session.wire_protocol import (
    BASE_FLAGS,
    OUTPUT_FORMAT,
    base_arguments,
    deny_rules,
    permission_arguments,
    tool_event,
    translate_event,
)


class TranslateTests(unittest.TestCase):
    def test_the_init_line_names_the_session(self) -> None:
        events = translate_event({'type': 'system', 'subtype': 'init', 'session_id': 'abc'})
        self.assertEqual(events, [{'type': TURN_EVENT_SESSION_STARTED, 'thread_id': 'abc'}])

    def test_other_system_lines_and_an_init_without_an_id_say_nothing(self) -> None:
        self.assertEqual(translate_event({'type': 'system', 'subtype': 'compact'}), [])
        self.assertEqual(translate_event({'type': 'system', 'subtype': 'init'}), [])

    def test_an_assistant_message_becomes_one_item_per_block(self) -> None:
        events = translate_event({'type': 'assistant', 'message': {'content': [
            {'type': 'text', 'text': 'Here you go'},
            {'type': 'thinking', 'thinking': 'let me see'},
            {'type': 'tool_use', 'name': 'run_terminal_cmd', 'input': {'command': 'ls'}},
            {'type': 'text', 'text': '   '},
            {'type': 'unknown'},
            'not a block',
        ]}})
        kinds = [event['item']['type'] for event in events]
        self.assertEqual(kinds, [TURN_ITEM_AGENT_MESSAGE, TURN_ITEM_REASONING, TURN_ITEM_COMMAND_EXECUTION])
        self.assertTrue(all(event['type'] == TURN_EVENT_ITEM_COMPLETED for event in events))
        self.assertEqual(events[0]['item']['text'], 'Here you go')

    def test_an_assistant_line_with_no_content_says_nothing(self) -> None:
        self.assertEqual(translate_event({'type': 'assistant'}), [])
        self.assertEqual(translate_event({'type': 'assistant', 'message': {'content': 'x'}}), [])

    def test_a_tool_result_line_says_nothing(self) -> None:
        self.assertEqual(translate_event({'type': 'user', 'message': {'content': []}}), [])

    def test_a_successful_result_completes_the_turn_with_its_usage(self) -> None:
        events = translate_event({
            'type': 'result', 'subtype': 'success', 'is_error': False,
            'result': 'done', 'usage': {'input_tokens': 3},
        })
        self.assertEqual(events, [{'type': TURN_EVENT_TURN_COMPLETED, 'usage': {'input_tokens': 3}}])

    def test_a_failed_result_carries_the_cli_s_own_errors(self) -> None:
        events = translate_event({
            'type': 'result', 'subtype': 'error_during_execution', 'is_error': True,
            'errors': ['Not signed in', '', 'set XAI_API_KEY'],
        })
        self.assertEqual(events[0]['type'], TURN_EVENT_TURN_FAILED)
        self.assertEqual(events[0]['error']['message'], 'Not signed in; set XAI_API_KEY')

    def test_a_failed_result_without_errors_falls_back_to_its_subtype(self) -> None:
        for subtype, expected in (
            ('error_max_turns', 'maximum number of turns'),
            ('error_during_execution', 'failed while it ran'),
            ('error_new_kind', 'ended with error_new_kind'),
        ):
            with self.subTest(subtype=subtype):
                event = translate_event({'type': 'result', 'subtype': subtype})[0]
                self.assertIn(expected, event['error']['message'])

    def test_a_failed_result_with_only_result_text_uses_it(self) -> None:
        event = translate_event({'type': 'result', 'is_error': True, 'result': 'refused'})[0]
        self.assertEqual(event['error']['message'], 'refused')


class ToolEventTests(unittest.TestCase):
    def test_tools_become_the_items_the_chat_draws(self) -> None:
        cases = (
            (('bash', {'cmd': 'pytest'}), TURN_ITEM_COMMAND_EXECUTION),
            (('edit_file', {'target_file': 'a.py'}), TURN_ITEM_FILE_CHANGE),
            (('write', {'file_path': 'b.py'}), TURN_ITEM_FILE_CHANGE),
            (('web_search', {'query': 'q'}), TURN_ITEM_WEB_SEARCH),
            (('read_file', {'path': 'c.py'}), TURN_ITEM_MCP_TOOL_CALL),
            (('mystery', None), TURN_ITEM_MCP_TOOL_CALL),
        )
        for (name, arguments), expected in cases:
            with self.subTest(name=name):
                self.assertEqual(tool_event(name, arguments)['item']['type'], expected)


class ArgvTests(unittest.TestCase):
    def test_every_run_reads_the_parsed_format_without_self_updating(self) -> None:
        self.assertEqual(base_arguments('grok'), ['grok', *BASE_FLAGS])
        self.assertIn(OUTPUT_FORMAT, BASE_FLAGS)
        self.assertIn('--no-auto-update', BASE_FLAGS)
        self.assertIn('--trust', BASE_FLAGS)

    def test_the_floor_is_denied_at_each_git_level(self) -> None:
        strict = deny_rules(GIT_ACCESS_STRICT)
        local = deny_rules(GIT_ACCESS_LOCAL)
        self.assertIn('Bash(git push*)', strict)
        self.assertIn('Bash(git commit*)', strict)
        self.assertIn('Bash(git push*)', local)
        self.assertNotIn('Bash(git commit*)', local)
        self.assertIn('Bash(shutdown*)', strict)
        self.assertEqual(strict[0], '--deny')

    def test_the_workspace_posture_depends_on_the_sandbox_existing(self) -> None:
        with patch.object(wire_protocol, 'SANDBOX_SUPPORTED', True):
            self.assertEqual(
                permission_arguments(), ['--always-approve', '--sandbox', 'workspace'],
            )
            self.assertEqual(
                permission_arguments(sandbox='read-only'),
                ['--sandbox', 'read-only', '--deny', 'Edit', '--deny', 'Write'],
            )
        with patch.object(wire_protocol, 'SANDBOX_SUPPORTED', False):
            # No sandbox: never approve a shell command with nothing around it.
            self.assertEqual(permission_arguments(), ['--permission-mode', 'acceptEdits'])
            self.assertEqual(
                permission_arguments(sandbox='read-only'),
                ['--deny', 'Edit', '--deny', 'Write'],
            )


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
