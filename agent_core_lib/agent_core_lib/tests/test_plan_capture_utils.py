"""Tests for the generic ExitPlanMode plan extractor.

Pure + provider-agnostic: feeds ``recent_events()``-shaped stand-ins
(``event_type`` + ``raw.message.content`` blocks) and asserts the plan
markdown that comes back. The host-specific persistence (where the plan
is written) is tested in the host's own suite.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from agent_core_lib.agent_core_lib.helpers.plan_capture_utils import (
    extract_plan_from_events,
)


def _event(event_type, content):
    return SimpleNamespace(
        event_type=event_type,
        raw={'message': {'role': 'assistant', 'content': content}},
    )


def _exit_plan(plan=None, text_blocks=None):
    content = list(text_blocks or [])
    tool_input = {} if plan is None else {'plan': plan}
    content.append({'type': 'tool_use', 'name': 'ExitPlanMode', 'input': tool_input})
    return _event('assistant', content)


class ExtractPlanFromEventsTests(unittest.TestCase):

    def test_plan_pulled_from_exit_plan_mode_input(self) -> None:
        events = [_exit_plan(plan='# Plan\n1. Do X')]
        self.assertEqual(extract_plan_from_events(events), '# Plan\n1. Do X')

    def test_falls_back_to_assistant_text_when_no_plan_field(self) -> None:
        events = [_exit_plan(
            plan=None,
            text_blocks=[{'type': 'text', 'text': 'Here is my plan: step 1'}],
        )]
        self.assertEqual(
            extract_plan_from_events(events), 'Here is my plan: step 1')

    def test_returns_latest_plan_when_multiple(self) -> None:
        events = [
            _exit_plan(plan='# Old plan'),
            _event('assistant', [{'type': 'text', 'text': 'thinking'}]),
            _exit_plan(plan='# New plan'),
        ]
        self.assertEqual(extract_plan_from_events(events), '# New plan')

    def test_tool_name_matched_case_insensitively(self) -> None:
        ev = _event('assistant', [
            {'type': 'tool_use', 'name': 'exitplanmode', 'input': {'plan': 'P'}},
        ])
        self.assertEqual(extract_plan_from_events([ev]), 'P')

    def test_no_exit_plan_mode_returns_empty(self) -> None:
        events = [_event('assistant', [{'type': 'text', 'text': 'just chatting'}])]
        self.assertEqual(extract_plan_from_events(events), '')

    def test_other_tool_use_is_ignored(self) -> None:
        ev = _event('assistant', [
            {'type': 'tool_use', 'name': 'Edit', 'input': {'plan': 'not a plan'}},
        ])
        self.assertEqual(extract_plan_from_events([ev]), '')

    def test_empty_or_malformed_events_return_empty(self) -> None:
        self.assertEqual(extract_plan_from_events([]), '')
        self.assertEqual(extract_plan_from_events(None), '')
        self.assertEqual(
            extract_plan_from_events([SimpleNamespace(raw=None)]), '')
        self.assertEqual(
            extract_plan_from_events([SimpleNamespace(raw={'message': 'x'})]), '')

    def test_empty_plan_field_falls_back_then_empty(self) -> None:
        # plan='' (empty string) + no text blocks → nothing to show.
        events = [_exit_plan(plan='')]
        self.assertEqual(extract_plan_from_events(events), '')

    def test_plan_captured_from_a_plans_file_write(self) -> None:
        # Newer CLIs persist the finalized plan by writing it to
        # ``~/.claude/plans/<slug>.md`` instead of the ExitPlanMode input —
        # the "made the plan but the host never shows it" report. Capture it.
        ev = _event('assistant', [
            {'type': 'tool_use', 'name': 'Write', 'input': {
                'file_path': '/Users/x/.claude/plans/zazzy-sprout.md',
                'content': '# Plan\n1. Build run-server.sh',
            }},
        ])
        self.assertEqual(extract_plan_from_events([ev]), '# Plan\n1. Build run-server.sh')

    def test_windows_plans_path_write_is_captured(self) -> None:
        ev = _event('assistant', [
            {'type': 'tool_use', 'name': 'create_file', 'input': {
                'file_path': 'C:\\Users\\shubh\\.claude\\plans\\x.md',
                'content': '# Windows plan',
            }},
        ])
        self.assertEqual(extract_plan_from_events([ev]), '# Windows plan')

    def test_a_non_plans_md_write_is_not_treated_as_a_plan(self) -> None:
        ev = _event('assistant', [
            {'type': 'tool_use', 'name': 'Write', 'input': {
                'file_path': '/wks/repo/src/notes.md', 'content': 'not a plan',
            }},
        ])
        self.assertEqual(extract_plan_from_events([ev]), '')

    def test_exit_plan_mode_still_wins_over_a_plans_file_write(self) -> None:
        ev = _event('assistant', [
            {'type': 'tool_use', 'name': 'Write', 'input': {
                'file_path': '/x/.claude/plans/a.md', 'content': 'file plan'}},
            {'type': 'tool_use', 'name': 'ExitPlanMode', 'input': {'plan': 'inline plan'}},
        ])
        self.assertEqual(extract_plan_from_events([ev]), 'inline plan')


if __name__ == '__main__':
    unittest.main()
