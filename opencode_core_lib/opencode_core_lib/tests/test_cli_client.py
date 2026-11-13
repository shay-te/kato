"""``OpenCodeCliClient``: health checks, the argv and env it builds, every spawn path."""

from __future__ import annotations

import json
import subprocess
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from agent_core_lib.agent_core_lib.data.fields import ImplementationFields
from agent_core_lib.agent_core_lib.helpers.cancellable_process import ProcessCancelled
from opencode_core_lib.opencode_core_lib import cli_client as module
from opencode_core_lib.opencode_core_lib.cli_client import OpenCodeCliClient
from opencode_core_lib.opencode_core_lib.helpers.cli_generation import (
    GENERATION_V1,
    GENERATION_V2,
)

_RUN = 'opencode_core_lib.opencode_core_lib.cli_client.subprocess.run'
_WHICH = 'opencode_core_lib.opencode_core_lib.cli_client.shutil.which'


def _completed(returncode=0, stdout='', stderr=''):
    return subprocess.CompletedProcess(args=['opencode'], returncode=returncode, stdout=stdout, stderr=stderr)


_OK = '\n'.join(json.dumps(line) for line in (
    {'type': 'step_start', 'sessionID': 'ses_9', 'part': {}},
    {'type': 'text', 'sessionID': 'ses_9', 'part': {'type': 'text', 'text': 'all done'}},
))


class ValidateConnectionTests(unittest.TestCase):
    def test_a_missing_binary_names_the_install_commands(self) -> None:
        with patch(_WHICH, return_value=None):
            with self.assertRaises(RuntimeError) as caught:
                OpenCodeCliClient().validate_connection()
        self.assertIn('@opencode/cli', str(caught.exception))
        self.assertIn('opencode auth login', str(caught.exception))

    def test_a_working_cli_with_the_json_format_passes(self) -> None:
        with patch(_WHICH, return_value='/bin/opencode'), patch(_RUN, side_effect=[
            _completed(stdout='2.2.7'), _completed(stdout='--format  json output'),
        ]) as run:
            OpenCodeCliClient().validate_connection()
        self.assertEqual(run.call_args_list[1].args[0][-2:], ['run', '--help'])

    def test_a_cli_without_the_json_format_is_refused(self) -> None:
        with patch(_WHICH, return_value='/bin/opencode'), patch(_RUN, side_effect=[
            _completed(stdout='0.1.0'), _completed(stdout='run [message]'),
        ]):
            with self.assertRaises(RuntimeError) as caught:
                OpenCodeCliClient().validate_connection()
        self.assertIn('too old', str(caught.exception))

    def test_a_help_probe_that_cannot_run_is_not_a_failure(self) -> None:
        for second in (subprocess.TimeoutExpired('opencode', 1), _completed(returncode=2)):
            with self.subTest(second=second):
                with patch(_WHICH, return_value='/bin/opencode'), patch(
                    _RUN, side_effect=[_completed(stdout='2.0.0'), second],
                ):
                    OpenCodeCliClient().validate_connection()

    def test_a_version_probe_that_fails_or_crashes_is_reported(self) -> None:
        with patch(_WHICH, return_value='/bin/opencode'), patch(
            _RUN, return_value=_completed(returncode=1, stderr='boom'),
        ):
            with self.assertRaises(RuntimeError) as caught:
                OpenCodeCliClient().validate_connection()
        self.assertIn('boom', str(caught.exception))
        with patch(_WHICH, return_value='/bin/opencode'), patch(_RUN, side_effect=OSError('nope')):
            with self.assertRaises(RuntimeError):
                OpenCodeCliClient().validate_connection()

    def test_docker_bypass_and_a_container_are_refused(self) -> None:
        for kwargs in ({'docker_mode_on': True}, {'bypass_permissions': True}):
            with self.subTest(**kwargs):
                with self.assertRaises(RuntimeError) as caught:
                    OpenCodeCliClient(**kwargs).validate_connection()
                self.assertIn('no Docker sandbox image', str(caught.exception))
        with patch.object(OpenCodeCliClient, '_running_inside_docker', return_value=True):
            with self.assertRaises(RuntimeError):
                OpenCodeCliClient().validate_connection()


class BuildCommandTests(unittest.TestCase):
    def _command(self, client=None, generation=GENERATION_V2, **kwargs):
        client = client or OpenCodeCliClient()
        with patch.object(module, 'cli_generation', return_value=generation):
            return client._build_command(
                additional_dirs=[], agent_session_id=kwargs.pop('agent_session_id', ''),
                resolve_binary=False, **kwargs,
            )

    def test_a_fresh_v2_run(self) -> None:
        self.assertEqual(self._command(), ['opencode', 'run', '--format', 'json', '--standalone'])

    def test_resume_model_effort_and_extra_args(self) -> None:
        client = OpenCodeCliClient(model='openai/gpt-5', effort='high', extra_args=['--thinking'])
        command = self._command(client, agent_session_id='ses_1')
        self.assertEqual(command[command.index('--session') + 1], 'ses_1')
        self.assertEqual(command[command.index('-m') + 1], 'openai/gpt-5#high')
        self.assertEqual(command[-1], '--thinking')
        v1 = self._command(client, generation=GENERATION_V1, model='openai/gpt-4o')
        self.assertEqual(v1[v1.index('-m') + 1], 'openai/gpt-4o')
        self.assertEqual(v1[v1.index('--variant') + 1], 'high')
        self.assertNotIn('--standalone', v1)


class RunPromptTests(unittest.TestCase):
    def _client(self, **kwargs) -> OpenCodeCliClient:
        client = OpenCodeCliClient(**kwargs)
        client._binary_path = 'opencode'
        return client

    def _patched(self, **kwargs):
        return patch.object(module, 'cli_generation', return_value=GENERATION_V2), patch(_RUN, **kwargs)

    def test_a_run_sends_the_prompt_on_stdin_with_rules_in_the_env(self) -> None:
        generation, run_patch = self._patched(return_value=_completed(stdout=_OK))
        with generation, run_patch as run, patch.object(module, 'read_lessons_file', return_value=''):
            answer = self._client().investigate('look around', cwd='/work/repo')
        self.assertEqual(answer, 'all done')
        self.assertEqual(run.call_args.kwargs['input'], 'look around')
        env = run.call_args.kwargs['env']
        self.assertEqual(env['PWD'], '/work/repo')
        self.assertEqual(json.loads(env['OPENCODE_CONFIG_CONTENT'])['permission']['edit'], 'deny')

    def test_implement_task_returns_the_host_result_shape(self) -> None:
        task = SimpleNamespace(id='T-1', summary='Fix it', description='broken', tags=[])
        generation, run_patch = self._patched(return_value=_completed(stdout=_OK))
        with generation, run_patch as run, patch.object(module, 'read_lessons_file', return_value='LESSONS'):
            result = self._client().implement_task(task)
        self.assertTrue(result[ImplementationFields.SUCCESS])
        self.assertEqual(result[ImplementationFields.AGENT_SESSION_ID], 'ses_9')
        env = run.call_args.kwargs['env']
        self.assertEqual(json.loads(env['OPENCODE_CONFIG_CONTENT'])['permission']['edit'], 'allow')

    def test_an_error_line_or_exit_raises_with_the_detail(self) -> None:
        failed = json.dumps({'type': 'error', 'error': {'message': 'No provider key'}})
        for completed, expected in ((_completed(stdout=failed), 'No provider key'),
                                    (_completed(returncode=1, stderr='bad'), 'bad')):
            generation, run_patch = self._patched(return_value=completed)
            with self.subTest(expected=expected), generation, run_patch:
                with self.assertRaises(RuntimeError) as caught:
                    self._client()._run_prompt(prompt='p', cwd='', additional_dirs=[])
                self.assertIn(expected, str(caught.exception))

    def test_timeouts_launch_failures_and_cancels(self) -> None:
        generation, run_patch = self._patched(side_effect=subprocess.TimeoutExpired('o', 5))
        with generation, run_patch, self.assertRaises(TimeoutError):
            self._client()._run_prompt(prompt='p', cwd='', additional_dirs=[], timeout_seconds=5)
        generation, run_patch = self._patched(side_effect=OSError('denied'))
        with generation, run_patch, self.assertRaises(RuntimeError):
            self._client()._run_prompt(prompt='p', cwd='', additional_dirs=[])
        cancel = threading.Event()
        with patch.object(module, 'cli_generation', return_value=GENERATION_V2), patch.object(
            module, 'run_cancellable', return_value=_completed(stdout=_OK),
        ) as runner:
            self._client()._run_prompt(prompt='p', cwd='/w', additional_dirs=[], cancel_event=cancel)
        self.assertEqual(runner.call_args.kwargs['input_text'], 'p')
        with patch.object(module, 'cli_generation', return_value=GENERATION_V2), patch.object(
            module, 'run_cancellable', side_effect=ProcessCancelled('stop'),
        ), self.assertRaises(ProcessCancelled):
            self._client()._run_prompt(prompt='p', cwd='/w', additional_dirs=[], cancel_event=cancel)

    def test_docker_or_bypass_refuses_the_spawn_itself(self) -> None:
        for kwargs in ({'docker_mode_on': True}, {'bypass_permissions': True}):
            with self.subTest(**kwargs), patch(_RUN) as run:
                with self.assertRaises(RuntimeError):
                    self._client(**kwargs)._run_prompt(prompt='p', cwd='', additional_dirs=[])
                run.assert_not_called()


class SmokeAndConstructionTests(unittest.TestCase):
    def test_the_smoke_test_passes_fails_or_times_out(self) -> None:
        client = OpenCodeCliClient()
        with patch.object(module, 'cli_generation', return_value=GENERATION_V2):
            with patch(_RUN, return_value=_completed(stdout=_OK)) as run:
                client._run_model_access_validation()
            self.assertEqual(run.call_args.kwargs['input'], client.SMOKE_TEST_PROMPT)
            with patch(_RUN, return_value=_completed(returncode=2, stderr='no model')):
                with self.assertRaises(RuntimeError):
                    client._run_model_access_validation()
            with patch(_RUN, side_effect=subprocess.TimeoutExpired('o', 1)):
                with self.assertRaises(RuntimeError):
                    client._run_model_access_validation()

    def test_knobs_opencode_cannot_honour_are_named_once(self) -> None:
        with patch('agent_core_lib.agent_core_lib.per_turn_cli_shared.configure_logger') as factory:
            logger = factory.return_value = MagicMock()
            OpenCodeCliClient(max_turns=3)
        self.assertIn('OpenCode backend ignores', logger.info.call_args.args[0])

    def test_the_addendum_rides_in_the_prompt_and_the_framing_is_bound(self) -> None:
        client = OpenCodeCliClient()
        with patch.object(module, 'read_lessons_file', return_value='READ THE LESSONS'):
            self.assertIn('READ THE LESSONS', client._system_prompt_addendum())
        self.assertIn('x', client._wrap_untrusted('x', source_path='task:T'))
        self.assertIn('y', OpenCodeCliClient._wrap_untrusted_text('y', source_path='task:T'))


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
