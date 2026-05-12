"""Final coverage for git_core_lib — GitClientMixin subprocess paths +
git_clean_utils + text_utils."""

from __future__ import annotations

import logging
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from git_core_lib.git_core_lib.client.git_client import GitClientMixin


class _ConcreteGitClient(GitClientMixin):
    """Concrete subclass for testing the mixin."""

    def __init__(self) -> None:
        self.logger = logging.getLogger('test_git_client_mixin')


class ValidateGitExecutableTests(unittest.TestCase):
    def test_returns_silently_when_git_on_path(self) -> None:
        # Line 52-53: ``if shutil.which('git'): return``.
        with patch(
            'git_core_lib.git_core_lib.client.git_client.shutil.which',
            return_value='/usr/bin/git',
        ):
            GitClientMixin._validate_git_executable()

    def test_raises_when_git_missing_from_path(self) -> None:
        # Line 54.
        with patch(
            'git_core_lib.git_core_lib.client.git_client.shutil.which',
            return_value=None,
        ):
            with self.assertRaisesRegex(RuntimeError, 'git executable'):
                GitClientMixin._validate_git_executable()


class RunGitSubprocessTests(unittest.TestCase):
    def test_injects_http_auth_header_when_present(self) -> None:
        # Lines 86-89.
        client = _ConcreteGitClient()
        client._build_git_http_auth_header = MagicMock(return_value='Bearer abc')
        completed = MagicMock(returncode=0, stdout='', stderr='')
        with patch('subprocess.run', return_value=completed) as run, \
             patch.dict('os.environ', {}, clear=False):
            client._run_git_subprocess('/x', ['status'])
        env = run.call_args.kwargs['env']
        self.assertEqual(env['GIT_CONFIG_COUNT'], '1')
        self.assertEqual(env['GIT_CONFIG_KEY_0'], 'http.extraHeader')
        self.assertEqual(env['GIT_CONFIG_VALUE_0'], 'Bearer abc')

    def test_clears_http_auth_env_when_header_missing(self) -> None:
        # Lines 91-93.
        client = _ConcreteGitClient()
        completed = MagicMock(returncode=0, stdout='', stderr='')
        with patch('subprocess.run', return_value=completed) as run, \
             patch.dict('os.environ', {
                 'GIT_CONFIG_COUNT': '1',
                 'GIT_CONFIG_KEY_0': 'http.extraHeader',
                 'GIT_CONFIG_VALUE_0': 'Bearer old',
             }, clear=False):
            client._run_git_subprocess('/x', ['status'])
        env = run.call_args.kwargs['env']
        self.assertNotIn('GIT_CONFIG_COUNT', env)
        self.assertNotIn('GIT_CONFIG_KEY_0', env)
        self.assertNotIn('GIT_CONFIG_VALUE_0', env)


class RunGitTests(unittest.TestCase):
    def test_recovers_from_index_lock_and_retries(self) -> None:
        # Lines 117-122: index.lock detected → clear it → retry.
        client = _ConcreteGitClient()
        first = MagicMock(
            returncode=1, stdout='',
            stderr='fatal: Unable to create .git/index.lock: File exists',
        )
        second = MagicMock(returncode=0, stdout='ok', stderr='')
        with patch.object(client, '_run_git_subprocess',
                          side_effect=[first, second]), \
             patch.object(client, '_validate_git_executable'), \
             patch.object(client, '_clear_stale_git_index_lock',
                          return_value=True):
            result = client._run_git('/x', ['status'], 'op failed')
        self.assertEqual(result.stdout, 'ok')

    def test_raises_when_retry_after_index_lock_still_fails(self) -> None:
        # Line 123-124: retry also failed.
        client = _ConcreteGitClient()
        first = MagicMock(
            returncode=1, stdout='',
            stderr='fatal: Unable to create .git/index.lock: File exists',
        )
        second = MagicMock(
            returncode=1, stdout='', stderr='still broken',
        )
        with patch.object(client, '_run_git_subprocess',
                          side_effect=[first, second]), \
             patch.object(client, '_validate_git_executable'), \
             patch.object(client, '_clear_stale_git_index_lock',
                          return_value=True):
            with self.assertRaisesRegex(RuntimeError, 'still broken'):
                client._run_git('/x', ['status'], 'op failed')

    def test_uses_default_failure_message_when_no_stderr_or_stdout(self) -> None:
        # Line 116: ``'git command failed'`` fallback.
        client = _ConcreteGitClient()
        result = MagicMock(returncode=1, stdout='', stderr='')
        with patch.object(client, '_run_git_subprocess', return_value=result), \
             patch.object(client, '_validate_git_executable'):
            with self.assertRaisesRegex(RuntimeError, 'git command failed'):
                client._run_git('/x', ['status'], 'op failed')


class LeftRightCommitCountsTests(unittest.TestCase):
    def test_returns_tuple_of_counts(self) -> None:
        client = _ConcreteGitClient()
        with patch.object(client, '_git_stdout', return_value='3\t5'):
            self.assertEqual(
                client._left_right_commit_counts('/x', 'a', 'b'),
                (3, 5),
            )

    def test_raises_when_output_malformed(self) -> None:
        # Lines 163-166: ``if len(parts) != 2``.
        client = _ConcreteGitClient()
        with patch.object(client, '_git_stdout', return_value='only-one'):
            with self.assertRaisesRegex(RuntimeError, 'failed to parse'):
                client._left_right_commit_counts('/x', 'a', 'b')

    def test_raises_when_non_numeric_output(self) -> None:
        # Lines 169-173: ``except ValueError``.
        client = _ConcreteGitClient()
        with patch.object(client, '_git_stdout', return_value='abc xyz'):
            with self.assertRaisesRegex(RuntimeError, 'failed to parse'):
                client._left_right_commit_counts('/x', 'a', 'b')


class AheadCountTests(unittest.TestCase):
    def test_raises_on_non_numeric_count(self) -> None:
        # Lines 188-192.
        client = _ConcreteGitClient()
        with patch.object(client, '_git_stdout', return_value='abc'):
            with self.assertRaisesRegex(RuntimeError, 'failed to parse'):
                client._ahead_count('/x', 'main', 'feat/x')

    def test_treats_blank_output_as_zero(self) -> None:
        # Line 187: ``int(ahead_count_text or '0')``.
        client = _ConcreteGitClient()
        with patch.object(client, '_git_stdout', return_value=''):
            self.assertEqual(client._ahead_count('/x', 'main', 'feat/x'), 0)


class IsGitIndexLockErrorTests(unittest.TestCase):
    def test_true_for_index_lock_error(self) -> None:
        self.assertTrue(GitClientMixin._is_git_index_lock_error(
            'fatal: Unable to create .git/index.lock: File exists',
        ))

    def test_false_for_unrelated_error(self) -> None:
        self.assertFalse(GitClientMixin._is_git_index_lock_error(
            'Could not connect to remote',
        ))


class ClearStaleGitIndexLockTests(unittest.TestCase):
    def test_keeps_lock_when_git_process_running(self) -> None:
        # Lines 217-222: another git is using the repo → leave the lock.
        client = _ConcreteGitClient()
        client.logger = MagicMock()
        with patch.object(client, '_has_running_git_process', return_value=True):
            result = client._clear_stale_git_index_lock('/x')
        self.assertFalse(result)
        client.logger.warning.assert_called()

    def test_returns_false_when_lock_missing(self) -> None:
        # Lines 224-226: FileNotFoundError on unlink → False.
        client = _ConcreteGitClient()
        client.logger = MagicMock()
        with patch.object(client, '_has_running_git_process', return_value=False), \
             patch.object(Path, 'unlink', side_effect=FileNotFoundError):
            result = client._clear_stale_git_index_lock('/x')
        self.assertFalse(result)

    def test_removes_stale_lock_and_logs(self) -> None:
        # Lines 224-228: unlink succeeds → log + return True.
        client = _ConcreteGitClient()
        client.logger = MagicMock()
        with patch.object(client, '_has_running_git_process', return_value=False), \
             patch.object(Path, 'unlink', return_value=None):
            result = client._clear_stale_git_index_lock('/x')
        self.assertTrue(result)
        client.logger.warning.assert_called()


class HasRunningGitProcessTests(unittest.TestCase):
    def test_returns_false_on_ps_oserror(self) -> None:
        # Lines 242-243.
        with patch('subprocess.run', side_effect=OSError('ps not found')):
            self.assertFalse(
                GitClientMixin._has_running_git_process('/x'),
            )

    def test_returns_false_on_ps_nonzero(self) -> None:
        # Lines 244-245.
        result = MagicMock(returncode=1, stdout='')
        with patch('subprocess.run', return_value=result):
            self.assertFalse(
                GitClientMixin._has_running_git_process('/x'),
            )

    def test_returns_true_when_git_command_for_path_found(self) -> None:
        # Lines 251-252.
        result = MagicMock(
            returncode=0,
            stdout='bash\ngit -C /x status\nfish\n',
        )
        with patch('subprocess.run', return_value=result):
            self.assertTrue(
                GitClientMixin._has_running_git_process('/x'),
            )

    def test_skips_non_git_command_lines(self) -> None:
        # Lines 249-250: non-git command lines are skipped.
        result = MagicMock(
            returncode=0,
            stdout='vim file.txt\npython -m unittest\n',
        )
        with patch('subprocess.run', return_value=result):
            self.assertFalse(
                GitClientMixin._has_running_git_process('/x'),
            )


class PushBranchTests(unittest.TestCase):
    def test_dry_run_does_not_attempt_remote_sync(self) -> None:
        # Lines 266-273: dry_run path raises without sync attempt.
        client = _ConcreteGitClient()
        with patch.object(
            client, '_run_git',
            side_effect=RuntimeError('updates were rejected'),
        ) as run:
            with self.assertRaises(RuntimeError):
                client._push_branch('/x', 'feat/x', dry_run=True)
        # Only the dry-run push was attempted; no sync.
        self.assertEqual(run.call_count, 1)

    def test_recovers_from_non_fast_forward_by_rebasing(self) -> None:
        # Lines 274-285: sync + retry push.
        client = _ConcreteGitClient()
        client.logger = MagicMock()
        attempts = iter([
            RuntimeError('updates were rejected because the remote contains work'),
            None,  # successful retry
        ])

        def run_git_side(*_args, **_kwargs):
            value = next(attempts)
            if isinstance(value, Exception):
                raise value
            return MagicMock()

        with patch.object(client, '_run_git', side_effect=run_git_side), \
             patch.object(client, '_sync_branch_with_remote') as sync:
            client._push_branch('/x', 'feat/x')
        sync.assert_called_once()

    def test_re_raises_when_failure_is_not_non_fast_forward(self) -> None:
        # Line 273.
        client = _ConcreteGitClient()
        with patch.object(
            client, '_run_git',
            side_effect=RuntimeError('permission denied'),
        ):
            with self.assertRaisesRegex(RuntimeError, 'permission denied'):
                client._push_branch('/x', 'feat/x')


class SyncBranchWithRemoteTests(unittest.TestCase):
    def test_raises_when_remote_branch_not_fetched(self) -> None:
        # Lines 298-302.
        client = _ConcreteGitClient()
        with patch.object(client, '_run_git'), \
             patch.object(client, '_git_reference_exists', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'is not available locally'):
                client._sync_branch_with_remote('/x', 'feat/x')

    def test_rebase_runs_after_fetch_succeeds(self) -> None:
        client = _ConcreteGitClient()
        with patch.object(client, '_run_git'), \
             patch.object(client, '_git_reference_exists', return_value=True), \
             patch.object(client, '_rebase_branch_onto_remote') as rebase:
            client._sync_branch_with_remote('/x', 'feat/x')
        rebase.assert_called_once()


class RebaseAndAbortTests(unittest.TestCase):
    def test_aborts_rebase_on_failure(self) -> None:
        # Lines 319-321.
        client = _ConcreteGitClient()
        with patch.object(
            client, '_run_git',
            side_effect=RuntimeError('rebase conflict'),
        ), patch.object(client, '_abort_rebase_after_failure') as abort:
            with self.assertRaises(RuntimeError):
                client._rebase_branch_onto_remote('/x', 'feat/x', 'origin/feat/x')
        abort.assert_called_once()

    def test_abort_rebase_swallows_abort_exception(self) -> None:
        # Lines 336-341.
        client = _ConcreteGitClient()
        client.logger = MagicMock()
        with patch.object(
            client, '_run_git',
            side_effect=RuntimeError('abort failed'),
        ):
            # Should NOT re-raise.
            client._abort_rebase_after_failure('/x', 'feat/x')
        client.logger.warning.assert_called()


class IsNonFastForwardPushRejectionTests(unittest.TestCase):
    def test_recognizes_known_markers(self) -> None:
        # Line 345-346.
        self.assertTrue(GitClientMixin._is_non_fast_forward_push_rejection(
            RuntimeError('updates were rejected because the remote contains work'),
        ))
        self.assertTrue(GitClientMixin._is_non_fast_forward_push_rejection(
            RuntimeError('non-fast-forward push'),
        ))
        self.assertTrue(GitClientMixin._is_non_fast_forward_push_rejection(
            RuntimeError('hint: fetch first'),
        ))

    def test_does_not_match_unrelated_errors(self) -> None:
        self.assertFalse(GitClientMixin._is_non_fast_forward_push_rejection(
            RuntimeError('permission denied'),
        ))


class UsesHttpRemoteTests(unittest.TestCase):
    def test_recognizes_https_remotes(self) -> None:
        # Line 354.
        self.assertTrue(GitClientMixin._uses_http_remote(
            'https://github.com/o/r.git',
        ))
        self.assertTrue(GitClientMixin._uses_http_remote(
            'http://internal.git.example/r.git',
        ))

    def test_does_not_match_ssh_remotes(self) -> None:
        self.assertFalse(GitClientMixin._uses_http_remote(
            'git@github.com:o/r.git',
        ))


class InferDefaultBranchTests(unittest.TestCase):
    def test_returns_branch_from_symbolic_ref(self) -> None:
        result = MagicMock(returncode=0, stdout='refs/remotes/origin/main\n')
        with patch('subprocess.run', return_value=result), \
             patch.object(GitClientMixin, '_validate_git_executable'):
            self.assertEqual(
                GitClientMixin._infer_default_branch('/x'),
                'main',
            )

    def test_falls_through_to_branch_show_current(self) -> None:
        # Lines 386-390: first command fails → fall through to second.
        first = MagicMock(returncode=1, stdout='')
        second = MagicMock(returncode=0, stdout='develop\n')
        with patch('subprocess.run', side_effect=[first, second]), \
             patch.object(GitClientMixin, '_validate_git_executable'):
            self.assertEqual(
                GitClientMixin._infer_default_branch('/x'),
                'develop',
            )

    def test_raises_when_no_command_produces_output(self) -> None:
        # Lines 391-393.
        empty = MagicMock(returncode=1, stdout='')
        with patch('subprocess.run', return_value=empty), \
             patch.object(GitClientMixin, '_validate_git_executable'):
            with self.assertRaisesRegex(ValueError, 'unable to determine'):
                GitClientMixin._infer_default_branch('/x')


class GitCommandSafeDirectoryArgsTests(unittest.TestCase):
    def test_returns_empty_for_blank_path(self) -> None:
        # Line 60.
        self.assertEqual(GitClientMixin._git_safe_directory_args(''), [])
        self.assertEqual(GitClientMixin._git_safe_directory_args('   '), [])


class GitCleanUtilsTests(unittest.TestCase):
    def test_git_ready_command_summary_with_remote_sync(self) -> None:
        # Lines 69-74: include_remote_sync=True adds fetch + reset --hard.
        from git_core_lib.git_core_lib.helpers.git_clean_utils import (
            git_ready_command_summary,
        )
        result = git_ready_command_summary('main', include_remote_sync=True)
        self.assertIn('git fetch origin', result)
        self.assertIn('git reset --hard origin/main', result)
        self.assertIn('git clean -fd', result)

    def test_git_ready_command_summary_without_remote_sync(self) -> None:
        from git_core_lib.git_core_lib.helpers.git_clean_utils import (
            git_ready_command_summary,
        )
        result = git_ready_command_summary('main', include_remote_sync=False)
        self.assertNotIn('git fetch', result)
        self.assertNotIn('git reset --hard', result)
        self.assertIn('git checkout -f main', result)
        self.assertIn('git clean -fd', result)


class TextUtilsTests(unittest.TestCase):
    def test_text_from_attr_returns_default_when_attr_missing(self) -> None:
        # Line 14: ``default`` is used when attr is missing or empty.
        from git_core_lib.git_core_lib.helpers.text_utils import text_from_attr
        # Object with no attr → falls back to default.
        self.assertEqual(
            text_from_attr(SimpleNamespace(), 'missing', 'fallback'),
            'fallback',
        )


class HappyPathTests(unittest.TestCase):
    """Cover the simple success-path returns that the error-path tests
    bypass via mocks."""

    def test_run_git_returns_result_on_success(self) -> None:
        # Line 115.
        client = _ConcreteGitClient()
        result = MagicMock(returncode=0, stdout='ok', stderr='')
        with patch.object(client, '_run_git_subprocess', return_value=result), \
             patch.object(client, '_validate_git_executable'):
            self.assertIs(client._run_git('/x', ['status'], 'op'), result)

    def test_git_stdout_strips_trailing_whitespace(self) -> None:
        # Lines 133-134.
        client = _ConcreteGitClient()
        result = MagicMock(returncode=0, stdout='  output\n', stderr='')
        with patch.object(client, '_run_git', return_value=result):
            self.assertEqual(client._git_stdout('/x', ['rev-parse'], 'op'), 'output')

    def test_git_reference_exists_true_on_zero_returncode(self) -> None:
        # Lines 139-148.
        client = _ConcreteGitClient()
        result = MagicMock(returncode=0)
        with patch('subprocess.run', return_value=result):
            self.assertTrue(
                client._git_reference_exists('/x', 'main'),
            )

    def test_git_reference_exists_false_on_nonzero(self) -> None:
        client = _ConcreteGitClient()
        result = MagicMock(returncode=1)
        with patch('subprocess.run', return_value=result):
            self.assertFalse(
                client._git_reference_exists('/x', 'nonexistent'),
            )

    def test_current_branch_returns_stripped_output(self) -> None:
        # Line 195.
        client = _ConcreteGitClient()
        with patch.object(client, '_git_stdout', return_value='main'):
            self.assertEqual(client._current_branch('/x'), 'main')

    def test_working_tree_status_returns_stripped_output(self) -> None:
        # Line 202.
        client = _ConcreteGitClient()
        with patch.object(client, '_git_stdout', return_value=' M file.py'):
            self.assertEqual(
                client._working_tree_status('/x'), ' M file.py',
            )

    def test_pull_destination_branch_invokes_run_git(self) -> None:
        # Line 354.
        client = _ConcreteGitClient()
        with patch.object(client, '_run_git') as run_git:
            client._pull_destination_branch('/x', 'main')
        run_git.assert_called_once()
        # The git args are ``['pull', '--ff-only', 'origin', 'main']``.
        args = run_git.call_args.args[1]
        self.assertIn('pull', args)
        self.assertIn('--ff-only', args)


if __name__ == '__main__':
    unittest.main()
