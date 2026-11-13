"""The spawn-free half every per-turn CLI client shares, on a stand-in transport."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from agent_core_lib.agent_core_lib.data.fields import ImplementationFields
from agent_core_lib.agent_core_lib.helpers.command_floor import prompt_floor_rules
from agent_core_lib.agent_core_lib.per_turn_cli_shared import PerTurnCliAgentBehaviour


class _Client(PerTurnCliAgentBehaviour):
    DEFAULT_BINARY = 'fakecli'
    CLI_DISPLAY_NAME = 'Fake'
    SUPPORTED_EFFORT_LEVELS = frozenset({'low', 'high'})
    BYPASS_WARNING = 'bypass is on'
    IGNORED_KNOBS = ('allowed_tools', 'max_turns')
    IGNORED_KNOBS_HINT = 'no such flags.'
    SUBPROCESS_ENV = {'FAKE_FLAG': '1'}

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.runs = []
        self.payload = {'result': 'fine', ImplementationFields.SUCCESS: True}

    def _run_prompt(self, **kwargs):
        self.runs.append(kwargs)
        return dict(self.payload)

    def _translate_wire_event(self, raw):
        if raw.get('kind') == 'say':
            return [{'type': 'item.completed', 'item': {'type': 'agent_message', 'text': raw['text']}}]
        return [raw]

    def _wrap_untrusted(self, text, *, source_path):
        return f'<{source_path}>{text}</>'

    @classmethod
    def _wrap_untrusted_text(cls, text, *, source_path):
        return f'<{source_path}>{text}</>'


def _comment(**overrides):
    fields = dict(pull_request_id='9', comment_id='c', author='r', body='fix', file_path='a.py',
                  line_number=3, line_type='ADDED', commit_sha='', repository_local_path='/repo')
    fields.update(overrides)
    return SimpleNamespace(**fields)


class ConstructionTests(unittest.TestCase):
    def test_defaults_and_coercions(self) -> None:
        client = _Client(binary=' ', timeout_seconds=5, max_retries=0, effort='HIGH', max_turns='3')
        self.assertEqual(client._binary, 'fakecli')
        self.assertEqual(client._timeout_seconds, 60)
        self.assertEqual(client.max_retries, 1)
        self.assertEqual(client._effort, 'high')
        self.assertEqual(client._max_turns, 3)
        self.assertEqual(_Client(model=' m ').model, 'm')

    def test_bypass_and_ignored_knobs_are_each_said_once(self) -> None:
        with patch('agent_core_lib.agent_core_lib.per_turn_cli_shared.configure_logger') as factory:
            logger = factory.return_value = MagicMock()
            _Client(bypass_permissions=True, max_turns=2)
        logger.warning.assert_called_once_with('bypass is on')
        self.assertIn('Fake backend ignores allowed_tools / max_turns — no such flags.',
                      logger.info.call_args.args[0])
        with patch('agent_core_lib.agent_core_lib.per_turn_cli_shared.configure_logger') as factory:
            logger = factory.return_value = MagicMock()
            _Client()
        logger.info.assert_not_called()


class InvestigateTests(unittest.TestCase):
    def test_a_read_only_run_with_its_own_overrides(self) -> None:
        client = _Client(repository_root_path='/root')
        self.assertEqual(client.investigate(' look ', model='m2', timeout_seconds=9), 'fine')
        run = client.runs[0]
        self.assertEqual((run['sandbox_override'], run['cwd'], run['model'], run['timeout_seconds']),
                         ('read-only', '/root', 'm2', 9))
        self.assertEqual(run['task_id'], 'triage')

    def test_the_message_is_the_fallback_answer_and_an_empty_prompt_is_refused(self) -> None:
        client = _Client()
        client.payload = {ImplementationFields.MESSAGE: 'from message'}
        self.assertEqual(client.investigate('q', cwd='/w'), 'from message')
        with self.assertRaises(ValueError):
            client.investigate('   ')


class ReviewCommentTests(unittest.TestCase):
    def test_one_comment_uses_the_single_prompt_and_drops_the_cwd_from_extras(self) -> None:
        client = _Client()
        with patch.object(client, '_build_review_prompt', return_value='P1') as single:
            result = client.fix_review_comments(
                [_comment()], 'T-1', additional_dirs=['/repo', ' /other ', ''],
            )
        self.assertTrue(result[ImplementationFields.SUCCESS])
        self.assertEqual(single.call_args.kwargs['additional_dirs'], ['/other'])
        self.assertEqual(client.runs[0]['cwd'], '/repo')

    def test_several_comments_use_the_batch_prompt(self) -> None:
        client = _Client()
        with patch.object(client, '_build_review_comments_batch_prompt', return_value='PB') as batch:
            client.fix_review_comments([_comment(), _comment(comment_id='d')], 'T-1', mode='answer')
        self.assertEqual(batch.call_args.kwargs['mode'], 'answer')

    def test_no_comments_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            _Client().fix_review_comments([], 'T-1')


class PromptFragmentTests(unittest.TestCase):
    def test_the_guardrails_render_the_one_floor(self) -> None:
        self.assertIn(prompt_floor_rules(), _Client._tool_guardrails_text())
        self.assertIn('read-only mode', _Client._read_only_instruction())


class ResultTests(unittest.TestCase):
    def _completed(self, returncode=0, stdout='', stderr=''):
        return subprocess.CompletedProcess(['x'], returncode, stdout, stderr)

    def test_the_environment_adds_the_transport_s_variables(self) -> None:
        env = _Client()._build_subprocess_env()
        self.assertEqual(env['FAKE_FLAG'], '1')
        self.assertEqual(env.get('PATH'), os.environ.get('PATH'))

    def test_the_stream_is_translated_before_it_is_reduced(self) -> None:
        stdout = '\n'.join([json.dumps({'kind': 'say', 'text': 'translated'}),
                            json.dumps({'type': 'thread.started', 'thread_id': 's'}), 'noise'])
        payload = _Client()._parse_jsonl_payload(stdout)
        self.assertEqual((payload['result'], payload[ImplementationFields.AGENT_SESSION_ID]), ('translated', 's'))

    def test_a_reply_file_beats_the_stream_and_a_missing_one_falls_back(self) -> None:
        client = _Client()
        stream = json.dumps({'kind': 'say', 'text': 'from stream'})
        with tempfile.NamedTemporaryFile('w', delete=False, suffix='.txt') as handle:
            handle.write('from file\n')
        self.addCleanup(os.unlink, handle.name)
        result = client._parse_completed_process(self._completed(stdout=stream), log_label='L',
                                                 last_message_file=handle.name)
        self.assertEqual(result[ImplementationFields.MESSAGE], 'from file')
        fallback = client._parse_completed_process(self._completed(stdout=stream), log_label='L',
                                                   last_message_file='/no/such/file')
        self.assertEqual(fallback['summary'], 'from stream')
        empty = client._parse_completed_process(self._completed(), log_label='L')
        self.assertNotIn(ImplementationFields.MESSAGE, empty)
        with_id = client._parse_completed_process(
            self._completed(stdout=json.dumps({'type': 'thread.started', 'thread_id': 's9'})),
            log_label='L',
        )
        self.assertEqual(with_id[ImplementationFields.AGENT_SESSION_ID], 's9')

    def test_a_failed_exit_or_an_error_event_raises_with_the_best_detail(self) -> None:
        client = _Client()
        error = json.dumps({'type': 'error', 'message': 'model refused'})
        cases = (
            (self._completed(1, stdout=error), 'model refused'),
            (self._completed(2, stderr='stderr detail'), 'stderr detail'),
            (self._completed(3), 'no output'),
            (self._completed(0, stdout=error), 'reported an error: model refused'),
            (self._completed(0, stdout=json.dumps({'type': 'turn.failed'}), stderr='raw'), 'raw'),
            (self._completed(0, stdout=json.dumps({'type': 'turn.failed'})), 'unknown Fake CLI error'),
        )
        for completed, expected in cases:
            with self.subTest(expected=expected):
                with self.assertRaises(RuntimeError) as caught:
                    client._parse_completed_process(completed, log_label='L')
                self.assertIn(expected, str(caught.exception))

    def test_the_smoke_test_verdict(self) -> None:
        client = _Client()
        self.assertEqual(client._smoke_test_failure(self._completed()), '')
        self.assertIn('failed: boom', client._smoke_test_failure(self._completed(1, stderr='boom')))
        self.assertIn('unknown error', client._smoke_test_failure(self._completed(1)))
        error = json.dumps({'type': 'turn.failed', 'error': {'message': 'denied'}})
        self.assertIn('reported an error: denied', client._smoke_test_failure(self._completed(stdout=error)))
        self.assertIn('unknown Fake CLI error',
                      client._smoke_test_failure(self._completed(stdout=json.dumps({'type': 'turn.failed'}))))


class HookTests(unittest.TestCase):
    def test_the_spawn_side_hooks_are_abstract(self) -> None:
        base = PerTurnCliAgentBehaviour()
        with self.assertRaises(NotImplementedError):
            base._build_command()
        with self.assertRaises(NotImplementedError):
            base._translate_wire_event({})


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
