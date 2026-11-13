"""The shared per-turn vocabulary and the builders every translator uses."""

from __future__ import annotations

import unittest

from agent_core_lib.agent_core_lib.session import turn_events as events
from agent_core_lib.agent_core_lib.session.turn_events import TurnEvent


class TurnEventTests(unittest.TestCase):
    def test_an_event_reads_its_type_subtype_and_terminality(self) -> None:
        event = TurnEvent(raw={'type': 'turn.failed', 'subtype': 'x'})
        self.assertEqual((event.event_type, event.subtype, event.is_terminal), ('turn.failed', 'x', True))
        self.assertFalse(TurnEvent(raw={'type': 'item.completed'}).is_terminal)
        self.assertEqual(TurnEvent().event_type, '')
        self.assertEqual(TurnEvent(raw={'type': 'a'}).to_dict()['raw'], {'type': 'a'})

    def test_the_terminal_set_is_the_three_ends_of_a_turn(self) -> None:
        self.assertEqual(events.TERMINAL_TURN_EVENTS, {'turn.completed', 'turn.failed', 'turn.aborted'})


class BuilderTests(unittest.TestCase):
    def test_every_builder_makes_the_shape_the_chat_draws(self) -> None:
        self.assertEqual(events.session_started('s'), {'type': 'thread.started', 'thread_id': 's'})
        self.assertEqual(events.turn_started(), {'type': 'turn.started'})
        self.assertEqual(events.user_message('hi')['message']['content'][0], {'type': 'text', 'text': 'hi'})
        self.assertEqual(events.agent_message('a')['item'], {'type': 'agent_message', 'text': 'a'})
        self.assertEqual(events.reasoning('r')['item']['type'], 'reasoning')
        command = events.command_execution('ls', output='x', exit_code=0)['item']
        self.assertEqual((command['command'], command['aggregated_output'], command['exit_code']), ('ls', 'x', 0))
        self.assertNotIn('exit_code', events.command_execution('ls')['item'])
        self.assertEqual(events.file_change(['a.py', '', 'b.py'])['item']['changes'], [{'path': 'a.py'}, {'path': 'b.py'}])
        self.assertEqual(events.tool_call('grep', server='s')['item'], {'type': 'mcp_tool_call', 'tool': 'grep', 'server': 's'})
        self.assertEqual(events.web_search('q')['item']['query'], 'q')
        self.assertEqual(events.turn_completed({'n': 1}), {'type': 'turn.completed', 'usage': {'n': 1}})
        self.assertEqual(events.turn_completed(), {'type': 'turn.completed'})
        self.assertEqual(events.turn_failed('no'), {'type': 'turn.failed', 'error': {'message': 'no'}})
        self.assertEqual(events.error_event('e'), {'type': 'error', 'message': 'e'})


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
