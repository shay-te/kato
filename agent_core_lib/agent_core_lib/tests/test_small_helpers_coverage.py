"""Coverage tests for four small agent_core_lib helper modules.

Product-agnostic: no product imports, fake fixtures only. Asserts the
structural behavior of each helper, exercising both sides of every
branch and the documented edge cases.
"""
from __future__ import annotations

import logging
import unittest
from types import SimpleNamespace

from agent_core_lib.agent_core_lib.helpers.logging_utils import (
    configure_logger,
    get_workflow_root,
    set_workflow_root,
)
from agent_core_lib.agent_core_lib.helpers.resume_prompt_utils import (
    build_inputs_from_session,
)


class ConfigureLoggerTests(unittest.TestCase):
    # The root namespace is generic + product-agnostic by default; a host
    # overrides it via set_workflow_root (e.g. a host → 'myapp.workflow').
    def setUp(self):
        # Restore the default after any test that overrides the root, so the
        # module-global _root can't leak into sibling tests.
        self.addCleanup(set_workflow_root, '')

    def test_default_root_is_generic(self):
        self.assertEqual(get_workflow_root(), 'agent.workflow')
        self.assertEqual(configure_logger('').name, 'agent.workflow')
        self.assertEqual(configure_logger('Svc').name, 'agent.workflow.Svc')

    def test_set_workflow_root_overrides_and_resets(self):
        set_workflow_root('myapp.workflow')
        self.assertEqual(get_workflow_root(), 'myapp.workflow')
        self.assertEqual(configure_logger('Svc').name, 'myapp.workflow.Svc')
        # Blank resets to the generic default.
        set_workflow_root('')
        self.assertEqual(get_workflow_root(), 'agent.workflow')

    def test_empty_name_returns_base_logger(self):
        base = configure_logger('')
        self.assertIsInstance(base, logging.Logger)

    def test_whitespace_name_strips_to_base(self):
        base = configure_logger('')
        whitespace = configure_logger('   ')
        self.assertIs(whitespace, base)

    def test_suffix_produces_child_of_base(self):
        base = configure_logger('')
        child = configure_logger('x')
        self.assertIsInstance(base, logging.Logger)
        self.assertEqual(child.name, base.name + '.x')

    def test_named_suffix_relationship(self):
        base = configure_logger('')
        child = configure_logger('mysuffix')
        self.assertEqual(child.name, base.name + '.mysuffix')


def _event(event_type, raw):
    return SimpleNamespace(event_type=event_type, raw=raw)


def _assistant_raw(blocks):
    return {'message': {'role': 'assistant', 'content': blocks}}


class BuildInputsFromSessionBranchTests(unittest.TestCase):
    def test_non_user_non_assistant_event_is_ignored(self):
        # 222->214: event_type 'system' makes the elif False; loop continues
        # without touching last_user / last_assistant.
        events = [
            _event('system', {'message': {'content': [
                {'type': 'text', 'text': 'should be ignored'}
            ]}}),
        ]
        out = build_inputs_from_session(
            task_id='PROJ-1',
            task_summary='summary',
            branch_name='feature/x',
            workspace_path='/tmp/ws',
            repository_paths=['/tmp/ws/repo'],
            recent_events=events,
        )
        self.assertEqual(out.last_user_text, '')
        self.assertEqual(out.last_assistant_text, '')
        self.assertEqual(out.recent_assistant_texts, [])

    def test_non_user_non_assistant_does_not_override_real_turns(self):
        events = [
            _event('user', {'message': {'content': 'hi there'}}),
            _event('assistant', _assistant_raw([
                {'type': 'text', 'text': 'real answer'}
            ])),
            _event('system', {'message': {'content': [
                {'type': 'text', 'text': 'ignored noise'}
            ]}}),
        ]
        out = build_inputs_from_session(
            task_id='PROJ-1',
            task_summary='summary',
            branch_name='feature/x',
            workspace_path='/tmp/ws',
            repository_paths=[],
            recent_events=events,
        )
        self.assertEqual(out.last_user_text, 'hi there')
        self.assertEqual(out.last_assistant_text, 'real answer')
        self.assertEqual(out.recent_assistant_texts, ['real answer'])

    def test_whitespace_text_block_is_skipped_in_flatten(self):
        # 265->260: a {'type':'text','text':'   '} block flattens to '' so the
        # `if text:` is False and it is skipped; only the real block survives.
        events = [
            _event('assistant', _assistant_raw([
                {'type': 'text', 'text': '   '},
                {'type': 'text', 'text': 'kept text'},
            ])),
        ]
        out = build_inputs_from_session(
            task_id='PROJ-1',
            task_summary='summary',
            branch_name='feature/x',
            workspace_path='/tmp/ws',
            repository_paths=[],
            recent_events=events,
        )
        self.assertEqual(out.last_assistant_text, 'kept text')
        self.assertEqual(out.recent_assistant_texts, ['kept text'])

    def test_whitespace_block_dropped_between_two_real_blocks(self):
        # Reinforces 265->260: the empty block sits BETWEEN two real blocks,
        # so the skip happens mid-list. The two real blocks join with the
        # '\n\n' separator and the whitespace block leaves no trace.
        events = [
            _event('assistant', _assistant_raw([
                {'type': 'text', 'text': 'first'},
                {'type': 'text', 'text': '   '},
                {'type': 'text', 'text': 'second'},
            ])),
        ]
        out = build_inputs_from_session(
            task_id='PROJ-1',
            task_summary='summary',
            branch_name='feature/x',
            workspace_path='/tmp/ws',
            repository_paths=[],
            recent_events=events,
        )
        self.assertEqual(out.last_assistant_text, 'first\n\nsecond')
        self.assertEqual(out.recent_assistant_texts, ['first\n\nsecond'])

    def test_empty_recent_events_yields_empty_inputs(self):
        out = build_inputs_from_session(
            task_id='PROJ-1',
            task_summary='summary',
            branch_name='feature/x',
            workspace_path='/tmp/ws',
            repository_paths=None,
            recent_events=None,
        )
        self.assertEqual(out.last_user_text, '')
        self.assertEqual(out.last_assistant_text, '')
        self.assertEqual(out.recent_assistant_texts, [])
        self.assertEqual(out.repository_paths, [])

    def test_recent_assistant_texts_respects_max(self):
        events = [
            _event('assistant', _assistant_raw([
                {'type': 'text', 'text': f'turn {i}'}
            ]))
            for i in range(5)
        ]
        out = build_inputs_from_session(
            task_id='PROJ-1',
            task_summary='summary',
            branch_name='feature/x',
            workspace_path='/tmp/ws',
            repository_paths=[],
            recent_events=events,
            max_recent_assistant=2,
        )
        self.assertEqual(out.recent_assistant_texts, ['turn 3', 'turn 4'])
        self.assertEqual(out.last_assistant_text, 'turn 4')


if __name__ == '__main__':
    unittest.main()
