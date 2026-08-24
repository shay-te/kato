"""The shared one-shot skeleton: stdin, timeout, exit codes, output.

Every CLI agent runs a one-shot the same way and differs only in its command
and where the answer lands. These tests pin the shared half; each transport's
own tests pin the flags it builds.

The bias throughout: a failure must name the CLI and carry the CLI's own
message, because the operator reading it is debugging their CLI install, not
this code.
"""

from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from agent_core_lib.agent_core_lib.helpers.one_shot import (
    DEFAULT_TIMEOUT_SECONDS,
    AgentOneShotError,
    run_one_shot,
)


def _completed(returncode=0, stdout='', stderr=''):
    return subprocess.CompletedProcess(
        args=['fake'], returncode=returncode, stdout=stdout, stderr=stderr,
    )


class RunOneShotTests(unittest.TestCase):
    def test_sends_the_prompt_on_stdin_and_returns_stdout(self) -> None:
        with patch(
            'agent_core_lib.agent_core_lib.helpers.one_shot.subprocess.run',
            return_value=_completed(stdout='the answer'),
        ) as run:
            result = run_one_shot('a prompt', command=['fake', '-p'], cli_name='fake')

        self.assertEqual(result, 'the answer')
        self.assertEqual(run.call_args.args[0], ['fake', '-p'])
        self.assertEqual(run.call_args.kwargs['input'], 'a prompt')
        self.assertEqual(run.call_args.kwargs['timeout'], DEFAULT_TIMEOUT_SECONDS)

    def test_missing_stdout_is_an_empty_string_not_none(self) -> None:
        with patch(
            'agent_core_lib.agent_core_lib.helpers.one_shot.subprocess.run',
            return_value=_completed(stdout=None),
        ):
            self.assertEqual(run_one_shot('p', command=['fake'], cli_name='fake'), '')

    def test_cwd_is_passed_through_and_empty_means_inherit(self) -> None:
        with patch(
            'agent_core_lib.agent_core_lib.helpers.one_shot.subprocess.run',
            return_value=_completed(stdout='x'),
        ) as run:
            run_one_shot('p', command=['fake'], cli_name='fake', cwd='/scratch')
            self.assertEqual(run.call_args.kwargs['cwd'], '/scratch')
            run_one_shot('p', command=['fake'], cli_name='fake')
            self.assertIsNone(run.call_args.kwargs['cwd'])

    def test_read_output_hook_overrides_stdout(self) -> None:
        with patch(
            'agent_core_lib.agent_core_lib.helpers.one_shot.subprocess.run',
            return_value=_completed(stdout='ignored'),
        ):
            result = run_one_shot(
                'p', command=['fake'], cli_name='fake',
                read_output=lambda completed: 'from the file',
            )

        self.assertEqual(result, 'from the file')

    def test_a_timeout_names_the_cli_and_the_limit(self) -> None:
        with patch(
            'agent_core_lib.agent_core_lib.helpers.one_shot.subprocess.run',
            side_effect=subprocess.TimeoutExpired(cmd='fake', timeout=7),
        ):
            with self.assertRaises(AgentOneShotError) as caught:
                run_one_shot('p', command=['fake'], cli_name='fake', timeout_seconds=7)

        self.assertIn('fake one-shot did not finish within 7s', str(caught.exception))

    def test_a_missing_binary_names_the_binary(self) -> None:
        with patch(
            'agent_core_lib.agent_core_lib.helpers.one_shot.subprocess.run',
            side_effect=OSError('No such file'),
        ):
            with self.assertRaises(AgentOneShotError) as caught:
                run_one_shot('p', command=['nope', '-p'], cli_name='fake')

        self.assertIn('failed to invoke fake binary "nope"', str(caught.exception))

    def test_a_non_zero_exit_carries_stderr(self) -> None:
        with patch(
            'agent_core_lib.agent_core_lib.helpers.one_shot.subprocess.run',
            return_value=_completed(returncode=2, stderr='not logged in'),
        ):
            with self.assertRaises(AgentOneShotError) as caught:
                run_one_shot('p', command=['fake'], cli_name='fake')

        self.assertIn('fake one-shot exited 2: not logged in', str(caught.exception))

    def test_a_non_zero_exit_falls_back_to_stdout_then_to_a_placeholder(self) -> None:
        target = 'agent_core_lib.agent_core_lib.helpers.one_shot.subprocess.run'
        with patch(target, return_value=_completed(returncode=1, stdout='partial')):
            with self.assertRaises(AgentOneShotError) as caught:
                run_one_shot('p', command=['fake'], cli_name='fake')
            self.assertIn('partial', str(caught.exception))
        with patch(target, return_value=_completed(returncode=1)):
            with self.assertRaises(AgentOneShotError) as caught:
                run_one_shot('p', command=['fake'], cli_name='fake')
            self.assertIn('<no output>', str(caught.exception))

    def test_a_transport_error_type_is_raised_so_callers_can_catch_either(self) -> None:
        class _FakeCliError(AgentOneShotError):
            pass

        with patch(
            'agent_core_lib.agent_core_lib.helpers.one_shot.subprocess.run',
            side_effect=OSError('boom'),
        ):
            with self.assertRaises(_FakeCliError):
                run_one_shot(
                    'p', command=['fake'], cli_name='fake', error_type=_FakeCliError,
                )

    def test_the_read_output_hook_never_runs_for_a_failed_call(self) -> None:
        def _explode(completed):
            raise AssertionError('output must not be read after a non-zero exit')

        with patch(
            'agent_core_lib.agent_core_lib.helpers.one_shot.subprocess.run',
            return_value=_completed(returncode=3, stderr='nope'),
        ):
            with self.assertRaises(AgentOneShotError):
                run_one_shot(
                    'p', command=['fake'], cli_name='fake', read_output=_explode,
                )


if __name__ == '__main__':
    unittest.main()
