from __future__ import annotations

import json
import subprocess
import unittest
from unittest.mock import patch

from kato.client.claude.cli_client import ClaudeCliClient
from kato.data_layers.data.fields import ImplementationFields
from utils import build_review_comment, build_task


class _FakeRepo:
    def __init__(self, repo_id: str, local_path: str, destination_branch: str = 'main') -> None:
        self.id = repo_id
        self.local_path = local_path
        self.destination_branch = destination_branch


def _completed(stdout: str, stderr: str = '', returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=['claude'],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


class ClaudeCliClientTests(unittest.TestCase):
    def test_validate_connection_raises_when_binary_missing(self) -> None:
        client = ClaudeCliClient(binary='claude-not-installed-xyz')
        with patch('kato.client.claude.cli_client.shutil.which', return_value=None), \
             patch.object(ClaudeCliClient, '_running_inside_docker', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'was not found on PATH'):
                client.validate_connection()

    def test_validate_connection_rejects_running_inside_docker(self) -> None:
        client = ClaudeCliClient(binary='claude')
        with patch.object(ClaudeCliClient, '_running_inside_docker', return_value=True):
            with self.assertRaisesRegex(
                RuntimeError,
                'KATO_AGENT_BACKEND=claude is not supported inside Docker',
            ):
                client.validate_connection()

    def test_validate_connection_runs_version_probe(self) -> None:
        client = ClaudeCliClient(binary='claude')
        with patch(
            'kato.client.claude.cli_client.shutil.which',
            return_value='/usr/local/bin/claude',
        ), patch(
            'kato.client.claude.cli_client.subprocess.run',
            return_value=_completed('claude 1.0.0\n'),
        ) as mock_run:
            client.validate_connection()

        mock_run.assert_called_once()
        args, _ = mock_run.call_args
        self.assertEqual(args[0], ['claude', '--version'])

    def test_validate_connection_raises_when_version_probe_fails(self) -> None:
        client = ClaudeCliClient(binary='claude')
        with patch(
            'kato.client.claude.cli_client.shutil.which',
            return_value='/usr/local/bin/claude',
        ), patch(
            'kato.client.claude.cli_client.subprocess.run',
            return_value=_completed('', stderr='boom', returncode=1),
        ):
            with self.assertRaisesRegex(RuntimeError, 'failed to report a version'):
                client.validate_connection()

    def test_delete_and_stop_are_no_ops(self) -> None:
        client = ClaudeCliClient(binary='claude')
        # Both calls should return without raising.
        client.delete_conversation('any-id')
        client.stop_all_conversations()

    def test_implement_task_passes_prompt_via_stdin_and_parses_json(self) -> None:
        client = ClaudeCliClient(binary='claude', model='claude-opus-4-7')
        prepared = type(
            'Prepared',
            (),
            {
                'repositories': [_FakeRepo('repo1', '/tmp/repo1')],
                'repository_branches': {'repo1': 'feature/proj-1'},
                'branch_name': 'feature/proj-1',
            },
        )()
        completed = _completed(
            json.dumps(
                {
                    'is_error': False,
                    'result': 'done',
                    'session_id': 'sess-123',
                }
            )
        )
        with patch(
            'kato.client.claude.cli_client.subprocess.run',
            return_value=completed,
        ) as mock_run:
            result = client.implement_task(build_task(), prepared_task=prepared)

        self.assertTrue(result[ImplementationFields.SUCCESS])
        self.assertEqual(result[ImplementationFields.SESSION_ID], 'sess-123')
        self.assertEqual(result[ImplementationFields.MESSAGE], 'done')
        # Verify the prompt was supplied via stdin and the command shape
        kwargs = mock_run.call_args.kwargs
        self.assertEqual(kwargs['cwd'], '/tmp/repo1')
        self.assertIn('-p', mock_run.call_args.args[0])
        self.assertIn('--output-format', mock_run.call_args.args[0])
        self.assertIn('json', mock_run.call_args.args[0])
        self.assertIn('--model', mock_run.call_args.args[0])
        self.assertIn('claude-opus-4-7', mock_run.call_args.args[0])
        self.assertIn('Implement task PROJ-1', kwargs['input'])

    def test_implement_task_adds_extra_repository_dirs(self) -> None:
        client = ClaudeCliClient(binary='claude')
        prepared = type(
            'Prepared',
            (),
            {
                'repositories': [
                    _FakeRepo('repo1', '/tmp/repo1'),
                    _FakeRepo('repo2', '/tmp/repo2'),
                ],
                'repository_branches': {},
                'branch_name': 'feature/proj-1',
            },
        )()
        completed = _completed(json.dumps({'is_error': False, 'result': 'ok', 'session_id': ''}))
        with patch(
            'kato.client.claude.cli_client.subprocess.run',
            return_value=completed,
        ) as mock_run:
            client.implement_task(build_task(), prepared_task=prepared)

        cmd = mock_run.call_args.args[0]
        self.assertIn('--add-dir', cmd)
        # Only the second repo should be added; the first is the cwd.
        self.assertEqual(cmd.count('--add-dir'), 1)
        self.assertIn('/tmp/repo2', cmd)

    def test_implement_task_raises_on_non_zero_exit_code(self) -> None:
        client = ClaudeCliClient(binary='claude', repository_root_path='/tmp/x')
        completed = _completed('', stderr='exploded', returncode=2)
        with patch(
            'kato.client.claude.cli_client.subprocess.run',
            return_value=completed,
        ):
            with self.assertRaisesRegex(RuntimeError, 'exited with status 2'):
                client.implement_task(build_task())

    def test_implement_task_raises_when_payload_reports_error(self) -> None:
        client = ClaudeCliClient(binary='claude', repository_root_path='/tmp/x')
        completed = _completed(
            json.dumps({'is_error': True, 'result': 'rate limited', 'session_id': ''})
        )
        with patch(
            'kato.client.claude.cli_client.subprocess.run',
            return_value=completed,
        ):
            with self.assertRaisesRegex(RuntimeError, 'rate limited'):
                client.implement_task(build_task())

    def test_implement_task_raises_on_subprocess_timeout(self) -> None:
        client = ClaudeCliClient(binary='claude', timeout_seconds=60, repository_root_path='/tmp/x')
        with patch(
            'kato.client.claude.cli_client.subprocess.run',
            side_effect=subprocess.TimeoutExpired(cmd=['claude'], timeout=60),
        ):
            with self.assertRaises(TimeoutError):
                client.implement_task(build_task())

    def test_fix_review_comment_passes_session_via_resume(self) -> None:
        client = ClaudeCliClient(binary='claude', repository_root_path='/tmp/x')
        completed = _completed(
            json.dumps({'is_error': False, 'result': 'fix done', 'session_id': 'sess-2'})
        )
        with patch(
            'kato.client.claude.cli_client.subprocess.run',
            return_value=completed,
        ) as mock_run:
            result = client.fix_review_comment(
                build_review_comment(),
                'feature/proj-1',
                session_id='sess-1',
            )

        self.assertTrue(result[ImplementationFields.SUCCESS])
        cmd = mock_run.call_args.args[0]
        self.assertIn('--resume', cmd)
        self.assertIn('sess-1', cmd)

    def test_test_task_uses_testing_prompt(self) -> None:
        client = ClaudeCliClient(binary='claude', repository_root_path='/tmp/x')
        completed = _completed(
            json.dumps({'is_error': False, 'result': 'tested', 'session_id': ''})
        )
        with patch(
            'kato.client.claude.cli_client.subprocess.run',
            return_value=completed,
        ) as mock_run:
            client.test_task(build_task())

        prompt = mock_run.call_args.kwargs['input']
        self.assertIn('Validate the implementation for task PROJ-1', prompt)
        self.assertIn('Act as a separate testing agent', prompt)

    def test_payload_parsing_handles_trailing_text(self) -> None:
        client = ClaudeCliClient(binary='claude')
        stdout = 'log line\n' + json.dumps({'is_error': False, 'result': 'ok', 'session_id': 'a'})
        payload = client._parse_json_payload(stdout)
        self.assertEqual(payload['result'], 'ok')

    def test_command_includes_optional_flags(self) -> None:
        client = ClaudeCliClient(
            binary='claude',
            model='claude-opus-4-7',
            max_turns=5,
            allowed_tools='Edit,Write',
            disallowed_tools='Bash',
            bypass_permissions=False,
        )
        cmd = client._build_command(additional_dirs=['/tmp/extra'], session_id='abc')
        self.assertEqual(cmd[0], 'claude')
        self.assertIn('--max-turns', cmd)
        self.assertIn('5', cmd)
        self.assertIn('--allowedTools', cmd)
        self.assertIn('Edit,Write', cmd)
        self.assertIn('--disallowedTools', cmd)
        # The disallowed-tools value now always includes the non-overridable
        # git denylist plus whatever the operator passed in.
        idx = cmd.index('--disallowedTools')
        self.assertIn('Bash', cmd[idx + 1].split(','))
        self.assertIn('--permission-mode', cmd)
        self.assertIn('acceptEdits', cmd)
        self.assertIn('--add-dir', cmd)
        self.assertIn('/tmp/extra', cmd)
        self.assertIn('--resume', cmd)
        self.assertIn('abc', cmd)

    def test_default_safe_mode_uses_acceptEdits_and_default_allowlist(self) -> None:
        client = ClaudeCliClient(binary='claude')
        cmd = client._build_command(additional_dirs=[], session_id='')
        self.assertIn('--permission-mode', cmd)
        self.assertIn('acceptEdits', cmd)
        self.assertIn('--allowedTools', cmd)
        self.assertIn('Edit,Write,Read,Bash,Glob,Grep', cmd)
        self.assertNotIn('bypassPermissions', cmd)

    def test_bypass_permissions_opts_into_dangerous_mode(self) -> None:
        client = ClaudeCliClient(binary='claude', bypass_permissions=True)
        cmd = client._build_command(additional_dirs=[], session_id='')
        self.assertIn('--permission-mode', cmd)
        self.assertIn('bypassPermissions', cmd)
        self.assertNotIn('acceptEdits', cmd)
        # When bypassing, no implicit allowlist is injected.
        self.assertNotIn('--allowedTools', cmd)


class ClaudeCliClientDockerModeTests(unittest.TestCase):
    """``KATO_CLAUDE_DOCKER`` plumbing for the per-task spawn paths.

    Docker mode wraps ``test_task`` and ``investigate`` spawns in the
    sandbox; boot-time validators (``validate_connection``,
    ``_run_model_access_validation``) deliberately stay on the host.
    """

    def test_docker_mode_off_does_not_invoke_sandbox_for_test_task(self) -> None:
        client = ClaudeCliClient(binary='claude', docker_mode_on=False)
        completed = _completed(
            json.dumps({'is_error': False, 'result': 'ok', 'session_id': 's'}),
        )
        with patch(
            'kato.client.claude.cli_client.subprocess.run',
            return_value=completed,
        ) as mock_run, patch(
            'kato.sandbox.manager.wrap_command',
        ) as mock_wrap:
            client.test_task(build_task())

        mock_wrap.assert_not_called()
        # Spawn argv is the raw claude command, not a docker run.
        spawn_argv = mock_run.call_args.args[0]
        self.assertEqual(spawn_argv[0], 'claude')

    def test_docker_mode_on_wraps_test_task_spawn_in_sandbox(self) -> None:
        client = ClaudeCliClient(
            binary='claude',
            docker_mode_on=True,
            repository_root_path='/tmp/repo',
        )
        completed = _completed(
            json.dumps({'is_error': False, 'result': 'ok', 'session_id': 's'}),
        )
        with patch(
            'kato.client.claude.cli_client.subprocess.run',
            return_value=completed,
        ) as mock_run, patch(
            'kato.sandbox.manager.ensure_image',
        ), patch(
            'kato.sandbox.manager.check_spawn_rate',
        ), patch(
            'kato.sandbox.manager.enforce_no_workspace_secrets',
        ), patch(
            'kato.sandbox.manager.record_spawn',
        ) as mock_record, patch(
            'kato.sandbox.manager.wrap_command',
            return_value=['docker', 'run', '--rm', 'kato-sandbox', 'claude'],
        ) as mock_wrap, patch(
            'kato.sandbox.manager.make_container_name',
            return_value='kato-sandbox-PROJ-1-abcd1234',
        ):
            client.test_task(build_task())

        mock_wrap.assert_called_once()
        wrap_kwargs = mock_wrap.call_args.kwargs
        self.assertEqual(wrap_kwargs['task_id'], 'PROJ-1')
        self.assertEqual(wrap_kwargs['container_name'], 'kato-sandbox-PROJ-1-abcd1234')
        # Audit log fires before the subprocess runs.
        mock_record.assert_called_once()
        # Spawn argv is the docker-wrapped command.
        spawn_argv = mock_run.call_args.args[0]
        self.assertEqual(spawn_argv[:2], ['docker', 'run'])

    def test_docker_mode_on_wraps_investigate_with_triage_task_id(self) -> None:
        client = ClaudeCliClient(
            binary='claude',
            docker_mode_on=True,
            repository_root_path='/tmp/repo',
        )
        completed = _completed(
            json.dumps({'is_error': False, 'result': 'verdict', 'session_id': 's'}),
        )
        with patch(
            'kato.client.claude.cli_client.subprocess.run',
            return_value=completed,
        ), patch(
            'kato.sandbox.manager.ensure_image',
        ), patch(
            'kato.sandbox.manager.check_spawn_rate',
        ), patch(
            'kato.sandbox.manager.enforce_no_workspace_secrets',
        ), patch(
            'kato.sandbox.manager.record_spawn',
        ) as mock_record, patch(
            'kato.sandbox.manager.wrap_command',
            return_value=['docker', 'run', '--rm', 'kato-sandbox', 'claude'],
        ) as mock_wrap, patch(
            'kato.sandbox.manager.make_container_name',
            return_value='kato-sandbox-triage-abcd1234',
        ) as mock_name:
            client.investigate('classify this task', cwd='/tmp/repo')

        mock_wrap.assert_called_once()
        # Triage carries no real task id — kato passes a synthetic
        # ``triage`` so the container name and audit row are still
        # grep-able rather than ``unknown``.
        mock_name.assert_called_once_with('triage')
        self.assertEqual(mock_wrap.call_args.kwargs['task_id'], 'triage')
        self.assertEqual(mock_record.call_args.kwargs['task_id'], 'triage')

    def test_docker_mode_on_does_NOT_wrap_validate_connection(self) -> None:
        """Boot-time validator: no workspace, no untrusted prompt — host only."""
        client = ClaudeCliClient(binary='claude', docker_mode_on=True)
        with patch(
            'kato.client.claude.cli_client.shutil.which',
            return_value='/usr/local/bin/claude',
        ), patch(
            'kato.client.claude.cli_client.subprocess.run',
            return_value=_completed('claude 1.0.0\n'),
        ) as mock_run, patch(
            'kato.sandbox.manager.wrap_command',
        ) as mock_wrap, patch.object(
            ClaudeCliClient, '_running_inside_docker', return_value=False,
        ):
            client.validate_connection()

        mock_wrap.assert_not_called()
        # Spawn argv is the raw ``claude --version``.
        spawn_argv = mock_run.call_args.args[0]
        self.assertEqual(spawn_argv, ['claude', '--version'])

    def test_docker_mode_on_does_NOT_wrap_model_access_validation(self) -> None:
        """Boot-time validator: fixed smoke-test prompt, no tools — host only."""
        client = ClaudeCliClient(binary='claude', docker_mode_on=True)
        with patch(
            'kato.client.claude.cli_client.subprocess.run',
            return_value=_completed(json.dumps({'is_error': False, 'result': 'ok'})),
        ) as mock_run, patch(
            'kato.sandbox.manager.wrap_command',
        ) as mock_wrap:
            client._run_model_access_validation()

        mock_wrap.assert_not_called()
        # Spawn argv is the raw ``claude -p ...``.
        spawn_argv = mock_run.call_args.args[0]
        self.assertEqual(spawn_argv[0], 'claude')

    def test_docker_mode_default_is_off(self) -> None:
        client = ClaudeCliClient(binary='claude')
        self.assertFalse(client._docker_mode_on)

    def test_docker_mode_independent_of_bypass_permissions(self) -> None:
        # docker=true, bypass=false (the new "structural-only" mode)
        client_a = ClaudeCliClient(
            binary='claude', docker_mode_on=True, bypass_permissions=False,
        )
        self.assertTrue(client_a._docker_mode_on)
        self.assertFalse(client_a._bypass_permissions)
        # docker=true, bypass=true (the original "bypass mode")
        client_b = ClaudeCliClient(
            binary='claude', docker_mode_on=True, bypass_permissions=True,
        )
        self.assertTrue(client_b._docker_mode_on)
        self.assertTrue(client_b._bypass_permissions)

    def test_docker_mode_off_does_not_append_sandbox_addendum(self) -> None:
        client = ClaudeCliClient(binary='claude', docker_mode_on=False)
        cmd = client._build_command(additional_dirs=[], session_id='')
        # No --append-system-prompt at all when there's no architecture
        # doc and docker mode is off.
        self.assertNotIn('--append-system-prompt', cmd)

    def test_docker_mode_on_appends_sandbox_addendum(self) -> None:
        from kato.sandbox.system_prompt import SANDBOX_SYSTEM_PROMPT_ADDENDUM

        client = ClaudeCliClient(binary='claude', docker_mode_on=True)
        cmd = client._build_command(additional_dirs=[], session_id='')
        self.assertIn('--append-system-prompt', cmd)
        idx = cmd.index('--append-system-prompt')
        self.assertEqual(cmd[idx + 1], SANDBOX_SYSTEM_PROMPT_ADDENDUM)

    def test_docker_plus_bypass_does_NOT_wrap_validate_connection(self) -> None:
        """docker=true AND bypass=true: boot-time validate_connection still on host.

        Operators in the original "bypass mode" (docker+bypass) might assume
        EVERYTHING gets sandbox-wrapped. The boot-time validators don't —
        they have no workspace and no untrusted prompt, so wrapping them
        adds startup latency for zero security benefit. Locks the design
        choice for the docker+bypass combination specifically (the
        docker-only case is locked by test_docker_mode_on_does_NOT_wrap_validate_connection).
        """
        client = ClaudeCliClient(
            binary='claude', docker_mode_on=True, bypass_permissions=True,
        )
        with patch(
            'kato.client.claude.cli_client.shutil.which',
            return_value='/usr/local/bin/claude',
        ), patch(
            'kato.client.claude.cli_client.subprocess.run',
            return_value=_completed('claude 1.0.0\n'),
        ) as mock_run, patch(
            'kato.sandbox.manager.wrap_command',
        ) as mock_wrap, patch.object(
            ClaudeCliClient, '_running_inside_docker', return_value=False,
        ):
            client.validate_connection()

        mock_wrap.assert_not_called()
        spawn_argv = mock_run.call_args.args[0]
        self.assertEqual(spawn_argv, ['claude', '--version'])

    def test_docker_plus_bypass_does_NOT_wrap_model_access_validation(self) -> None:
        """docker=true AND bypass=true: smoke-test prompt still on host.

        Same reasoning as test_docker_plus_bypass_does_NOT_wrap_validate_connection
        — the smoke test sends a fixed prompt with no tools enabled, so
        wrapping it buys nothing. Locks the design choice for docker+bypass.
        """
        client = ClaudeCliClient(
            binary='claude', docker_mode_on=True, bypass_permissions=True,
        )
        with patch(
            'kato.client.claude.cli_client.subprocess.run',
            return_value=_completed(json.dumps({'is_error': False, 'result': 'ok'})),
        ) as mock_run, patch(
            'kato.sandbox.manager.wrap_command',
        ) as mock_wrap:
            client._run_model_access_validation()

        mock_wrap.assert_not_called()
        spawn_argv = mock_run.call_args.args[0]
        self.assertEqual(spawn_argv[0], 'claude')


if __name__ == '__main__':
    unittest.main()
