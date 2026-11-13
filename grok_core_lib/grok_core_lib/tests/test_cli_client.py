"""``GrokCliClient``: health checks, the argv it builds, and every spawn path.

``subprocess.run`` / ``shutil.which`` are patched where the client spawns, so
these exercise the client's own decisions; ``test_flow.py`` runs it A-Z.
"""

from __future__ import annotations

import json
import subprocess
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from agent_core_lib.agent_core_lib.data.fields import ImplementationFields
from agent_core_lib.agent_core_lib.helpers.cancellable_process import ProcessCancelled
from grok_core_lib.grok_core_lib import cli_client as module
from grok_core_lib.grok_core_lib.cli_client import GrokCliClient, _rules

_RUN = 'grok_core_lib.grok_core_lib.cli_client.subprocess.run'
_WHICH = 'grok_core_lib.grok_core_lib.cli_client.shutil.which'


def _completed(returncode=0, stdout='', stderr=''):
    return subprocess.CompletedProcess(args=['grok'], returncode=returncode, stdout=stdout, stderr=stderr)


def _stream(*lines) -> str:
    return '\n'.join(json.dumps(line) for line in lines)


_OK = _stream(
    {'type': 'system', 'subtype': 'init', 'session_id': 'sess-9'},
    {'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'all done'}]}},
    {'type': 'result', 'subtype': 'success', 'is_error': False, 'result': 'all done'},
)


class ValidateConnectionTests(unittest.TestCase):
    def test_a_missing_binary_names_the_install_commands(self) -> None:
        with patch(_WHICH, return_value=None):
            with self.assertRaises(RuntimeError) as caught:
                GrokCliClient().validate_connection()
        self.assertIn('x.ai/cli/install.sh', str(caught.exception))
        self.assertIn('grok login', str(caught.exception))

    def test_a_working_cli_with_the_stream_format_passes(self) -> None:
        with patch(_WHICH, return_value='/bin/grok'), patch(_RUN, side_effect=[
            _completed(stdout='grok 1.0.45'),
            _completed(stdout='--output-format <streaming-messages-json>'),
        ]) as run:
            client = GrokCliClient()
            client.validate_connection()
        self.assertEqual(client._binary_path, '/bin/grok')
        self.assertEqual(run.call_args_list[1].args[0][-1], '--help')
        for call in run.call_args_list:
            self.assertIs(call.kwargs['stdin'], subprocess.DEVNULL)

    def test_a_cli_without_the_stream_format_is_refused(self) -> None:
        with patch(_WHICH, return_value='/bin/grok'), patch(_RUN, side_effect=[
            _completed(stdout='grok 0.1.0'), _completed(stdout='--output-format plain'),
        ]):
            with self.assertRaises(RuntimeError) as caught:
                GrokCliClient().validate_connection()
        self.assertIn('too old', str(caught.exception))

    def test_a_help_probe_that_cannot_run_is_not_a_failure(self) -> None:
        for second in (subprocess.TimeoutExpired('grok', 1), _completed(returncode=2)):
            with self.subTest(second=second):
                with patch(_WHICH, return_value='/bin/grok'), patch(
                    _RUN, side_effect=[_completed(stdout='grok 1.0'), second],
                ):
                    GrokCliClient().validate_connection()

    def test_a_version_probe_that_fails_or_crashes_is_reported(self) -> None:
        with patch(_WHICH, return_value='/bin/grok'), patch(
            _RUN, return_value=_completed(returncode=1, stderr='boom'),
        ):
            with self.assertRaises(RuntimeError) as caught:
                GrokCliClient().validate_connection()
        self.assertIn('boom', str(caught.exception))
        with patch(_WHICH, return_value='/bin/grok'), patch(_RUN, side_effect=OSError('nope')):
            with self.assertRaises(RuntimeError) as caught:
                GrokCliClient().validate_connection()
        self.assertIn('failed to launch', str(caught.exception))

    def test_docker_and_bypass_are_refused_never_run_unsandboxed(self) -> None:
        for kwargs in ({'docker_mode_on': True}, {'bypass_permissions': True}):
            with self.subTest(**kwargs):
                with self.assertRaises(RuntimeError) as caught:
                    GrokCliClient(**kwargs).validate_connection()
                self.assertIn('no Docker sandbox image', str(caught.exception))

    def test_inside_a_container_it_refuses(self) -> None:
        with patch.object(GrokCliClient, '_running_inside_docker', return_value=True):
            with self.assertRaises(RuntimeError) as caught:
                GrokCliClient().validate_connection()
        self.assertIn('not supported inside Docker', str(caught.exception))


class BuildCommandTests(unittest.TestCase):
    def _command(self, client=None, **kwargs):
        client = client or GrokCliClient(binary='grok')
        with patch.object(module, 'read_lessons_file', return_value=''):
            return client._build_command(
                additional_dirs=kwargs.pop('additional_dirs', []),
                agent_session_id=kwargs.pop('agent_session_id', ''),
                resolve_binary=False, **kwargs,
            )

    def test_a_fresh_run_names_its_prompt_working_directory_and_floor(self) -> None:
        command = self._command(prompt_file='/tmp/p.md', cwd='/work/repo')
        self.assertEqual(command[:2], ['grok', '--output-format'])
        self.assertEqual(command[command.index('--prompt-file') + 1], '/tmp/p.md')
        self.assertEqual(command[command.index('--cwd') + 1], '/work/repo')
        self.assertIn('Bash(git push*)', command)
        self.assertIn('Bash(git commit*)', command)  # one-shot runs are strict
        self.assertNotIn('--resume', command)

    def test_a_resumed_run_does_not_move_its_working_directory(self) -> None:
        command = self._command(agent_session_id='sess-1', cwd='/work/repo')
        self.assertEqual(command[command.index('--resume') + 1], 'sess-1')
        self.assertNotIn('--cwd', command)

    def test_model_effort_turns_and_tool_rules_reach_the_cli(self) -> None:
        client = GrokCliClient(
            model='grok-4.6', effort='high', max_turns=7,
            allowed_tools='Read,Bash(npm test, npm run lint)', disallowed_tools='WebFetch',
            extra_args=['--no-subagents'],
        )
        command = self._command(client)
        self.assertEqual(command[command.index('-m') + 1], 'grok-4.6')
        self.assertEqual(command[command.index('--reasoning-effort') + 1], 'high')
        self.assertEqual(command[command.index('--max-turns') + 1], '7')
        self.assertIn('Bash(npm test, npm run lint)', command)
        self.assertEqual(command[command.index('WebFetch') - 1], '--deny')
        self.assertEqual(command[-1], '--no-subagents')

    def test_a_per_run_model_outranks_the_configured_one(self) -> None:
        client = GrokCliClient(model='grok-4.5')
        command = self._command(client, model='grok-4.7')
        self.assertEqual(command[command.index('-m') + 1], 'grok-4.7')

    def test_a_read_only_run_gets_the_read_only_posture(self) -> None:
        command = self._command(sandbox_override='read-only')
        self.assertIn('--deny', command)
        self.assertIn('Edit', command)
        self.assertNotIn('--always-approve', command)

    def test_the_system_addendum_rides_in_rules(self) -> None:
        client = GrokCliClient()
        with patch.object(module, 'read_lessons_file', return_value='READ THE LESSONS'):
            command = client._build_command(additional_dirs=[], agent_session_id='')
            quiet = client._build_command(
                additional_dirs=[], agent_session_id='', include_system_prompt=False,
            )
        self.assertIn('READ THE LESSONS', command[command.index('--rules') + 1])
        self.assertNotIn('--rules', quiet)
        self.assertEqual(client._system_prompt_addendum(), '')
        with patch.object(module, '_system_rules', return_value=''):
            empty = client._build_command(additional_dirs=[], agent_session_id='')
        self.assertNotIn('--rules', empty)

    def test_rule_lists_split_only_outside_parentheses(self) -> None:
        self.assertEqual(_rules('Read, Bash(a, b),  ,Edit'), ['Read', 'Bash(a, b)', 'Edit'])
        self.assertEqual(_rules(''), [])
        self.assertEqual(_rules('Bash(x))'), ['Bash(x))'])


class RunPromptTests(unittest.TestCase):
    def _client(self, **kwargs) -> GrokCliClient:
        client = GrokCliClient(binary='grok', **kwargs)
        client._binary_path = 'grok'
        return client

    def test_a_run_parses_the_reply_and_the_session(self) -> None:
        with patch(_RUN, return_value=_completed(stdout=_OK)) as run, patch.object(
            module, 'read_lessons_file', return_value='',
        ):
            result = self._client().investigate('look around', cwd='/work')
        self.assertEqual(result, 'all done')
        argv = run.call_args.args[0]
        self.assertIn('--prompt-file', argv)
        self.assertIs(run.call_args.kwargs['stdin'], subprocess.DEVNULL)
        self.assertEqual(run.call_args.kwargs['env']['GROK_DISABLE_AUTOUPDATER'], '1')

    def test_implement_task_returns_the_host_result_shape(self) -> None:
        task = SimpleNamespace(id='T-1', summary='Fix it', description='It is broken', tags=[])
        with patch(_RUN, return_value=_completed(stdout=_OK)), patch.object(
            module, 'read_lessons_file', return_value='',
        ):
            result = self._client().implement_task(task)
        self.assertTrue(result[ImplementationFields.SUCCESS])
        self.assertEqual(result[ImplementationFields.AGENT_SESSION_ID], 'sess-9')

    def test_a_refused_run_raises_with_grok_s_words(self) -> None:
        failed = _stream({'type': 'result', 'subtype': 'error_during_execution',
                          'is_error': True, 'errors': ['Not signed in']})
        with patch(_RUN, return_value=_completed(stdout=failed)):
            with self.assertRaises(RuntimeError) as caught:
                self._client()._run_prompt(prompt='p', cwd='', additional_dirs=[])
        self.assertIn('Not signed in', str(caught.exception))

    def test_a_non_zero_exit_raises_with_the_detail(self) -> None:
        with patch(_RUN, return_value=_completed(returncode=1, stderr='bad flag')):
            with self.assertRaises(RuntimeError) as caught:
                self._client()._run_prompt(prompt='p', cwd='', additional_dirs=[])
        self.assertIn('bad flag', str(caught.exception))

    def test_timeouts_and_launch_failures_are_reported(self) -> None:
        with patch(_RUN, side_effect=subprocess.TimeoutExpired('grok', 5)):
            with self.assertRaises(TimeoutError):
                self._client()._run_prompt(prompt='p', cwd='', additional_dirs=[], timeout_seconds=5)
        with patch(_RUN, side_effect=OSError('denied')):
            with self.assertRaises(RuntimeError) as caught:
                self._client()._run_prompt(prompt='p', cwd='', additional_dirs=[])
        self.assertIn('denied', str(caught.exception))

    def test_a_cancellable_run_goes_through_the_cancellable_runner(self) -> None:
        cancel = threading.Event()
        with patch.object(module, 'run_cancellable', return_value=_completed(stdout=_OK)) as runner:
            self._client()._run_prompt(prompt='p', cwd='/w', additional_dirs=[], cancel_event=cancel)
        self.assertIs(runner.call_args.kwargs['cancel_event'], cancel)
        with patch.object(module, 'run_cancellable', side_effect=ProcessCancelled('stop')):
            with self.assertRaises(ProcessCancelled):
                self._client()._run_prompt(prompt='p', cwd='/w', additional_dirs=[], cancel_event=cancel)

    def test_docker_or_bypass_refuses_the_spawn_itself(self) -> None:
        for kwargs in ({'docker_mode_on': True}, {'bypass_permissions': True}):
            with self.subTest(**kwargs), patch(_RUN) as run:
                with self.assertRaises(RuntimeError):
                    self._client(**kwargs)._run_prompt(prompt='p', cwd='', additional_dirs=[])
                run.assert_not_called()


class SmokeTestTests(unittest.TestCase):
    def test_a_passing_smoke_run_raises_nothing(self) -> None:
        client = GrokCliClient()
        with patch(_RUN, return_value=_completed(stdout=_OK)) as run:
            client._run_model_access_validation()
        self.assertIn('--prompt-file', run.call_args.args[0])

    def test_a_failing_or_hung_smoke_run_raises(self) -> None:
        client = GrokCliClient()
        with patch(_RUN, return_value=_completed(returncode=3, stderr='no model')):
            with self.assertRaises(RuntimeError) as caught:
                client._run_model_access_validation()
        self.assertIn('no model', str(caught.exception))
        with patch(_RUN, side_effect=subprocess.TimeoutExpired('grok', 1)):
            with self.assertRaises(RuntimeError):
                client._run_model_access_validation()

    def test_it_runs_once_and_only_when_enabled(self) -> None:
        client = GrokCliClient(model_smoke_test_enabled=True)
        with patch.object(client, '_run_model_access_validation') as smoke:
            client._validate_model_smoke_test()
            client.validate_model_access()
        smoke.assert_called_once()


class ConstructionTests(unittest.TestCase):
    def test_knobs_grok_cannot_honour_are_named_once(self) -> None:
        with patch('agent_core_lib.agent_core_lib.per_turn_cli_shared.configure_logger') as factory:
            logger = factory.return_value = MagicMock()
            GrokCliClient(read_only_tools_on=True)
        self.assertIn('Grok backend ignores read_only_tools_on', logger.info.call_args.args[0])

    def test_bypass_warns_that_runs_will_be_refused(self) -> None:
        with patch('agent_core_lib.agent_core_lib.per_turn_cli_shared.configure_logger') as factory:
            logger = factory.return_value = MagicMock()
            GrokCliClient(bypass_permissions=True)
        self.assertIn('REFUSED', logger.warning.call_args.args[0])

    def test_an_unknown_effort_is_rejected_at_construction(self) -> None:
        with self.assertRaises(ValueError):
            GrokCliClient(effort='ludicrous')

    def test_the_untrusted_framing_is_bound(self) -> None:
        client = GrokCliClient()
        self.assertIn('x', client._wrap_untrusted('x', source_path='task:T'))
        self.assertIn('y', GrokCliClient._wrap_untrusted_text('y', source_path='task:T'))
        self.assertEqual(client.model, '')


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
