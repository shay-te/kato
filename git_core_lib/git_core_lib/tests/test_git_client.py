"""Coverage for ``git_core_lib/client/git_client.py`` (``GitClientMixin``).

Tests target the previously-uncovered branches: empty-args edge cases,
index-lock retry path, parse failures, push-rejection / rebase-abort,
``_has_running_git_process``, default-branch inference.

We use a minimal concrete subclass that provides ``logger``; all
subprocess interactions are mocked.
"""

from __future__ import annotations

import logging
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from git_core_lib.git_core_lib.client.git_client import GitClientMixin


class _Client(GitClientMixin):
    """Minimal concrete subclass with a logger."""

    def __init__(self) -> None:
        self.logger = logging.getLogger('test.git_client')


def _completed(returncode=0, stdout='', stderr=''):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


class BuildGitHttpAuthHeaderTests(unittest.TestCase):
    def test_default_returns_empty(self) -> None:
        # Line 46: default implementation — no auth header.
        client = _Client()
        self.assertEqual(client._build_git_http_auth_header(SimpleNamespace()), '')


class GitSafeDirectoryArgsTests(unittest.TestCase):
    def test_returns_empty_when_local_path_blank(self) -> None:
        # Line 60: empty path → no -c args.
        self.assertEqual(GitClientMixin._git_safe_directory_args(''), [])
        self.assertEqual(GitClientMixin._git_safe_directory_args('   '), [])

    def test_returns_safe_directory_arg_when_path_provided(self) -> None:
        result = GitClientMixin._git_safe_directory_args('/repo')
        self.assertEqual(result, ['-c', 'safe.directory=/repo'])


class RunGitRetryAfterLockReleaseTests(unittest.TestCase):
    def test_retries_after_clearing_stale_index_lock(self) -> None:
        # Line 123: index lock detected → clear and retry. If the retry
        # ALSO fails, the new failure_detail line on 123 fires.
        client = _Client()
        # Lock detection requires both 'index.lock' AND 'file exists'.
        lock_err = _completed(
            returncode=1,
            stderr='fatal: Unable to create .git/index.lock: File exists',
        )
        retry_err = _completed(returncode=1, stderr='still broken on retry')

        with patch.object(client, '_validate_git_executable'), \
             patch.object(client, '_run_git_subprocess', side_effect=[lock_err, retry_err]), \
             patch.object(client, '_clear_stale_git_index_lock', return_value=True):
            with self.assertRaisesRegex(RuntimeError, 'still broken on retry'):
                client._run_git('/repo', ['status'], 'op failed')


class LeftRightCommitCountsTests(unittest.TestCase):
    def test_raises_when_stdout_has_wrong_token_count(self) -> None:
        # Line 163: stdout doesn't split into exactly 2 → RuntimeError.
        client = _Client()
        with patch.object(client, '_git_stdout', return_value='only-one-token'):
            with self.assertRaisesRegex(RuntimeError, 'failed to parse commit counts'):
                client._left_right_commit_counts('/repo', 'a', 'b')

    def test_raises_when_tokens_are_not_integers(self) -> None:
        # Lines 169-170: int() raises → wrap as RuntimeError.
        client = _Client()
        with patch.object(client, '_git_stdout', return_value='not_a_number 7'):
            with self.assertRaisesRegex(RuntimeError, 'failed to parse commit counts'):
                client._left_right_commit_counts('/repo', 'a', 'b')


class AheadCountTests(unittest.TestCase):
    def test_raises_when_output_not_integer(self) -> None:
        # Lines 188-189: int() raises → wrap as RuntimeError.
        client = _Client()
        with patch.object(client, '_git_stdout', return_value='not_a_number'):
            with self.assertRaisesRegex(RuntimeError, 'failed to parse ahead count'):
                client._ahead_count('/repo', 'main', 'feat')


class HasRunningGitProcessTests(unittest.TestCase):
    def test_returns_false_on_oserror(self) -> None:
        # Lines 242-243: ``subprocess.run`` raises OSError → False.
        with patch(
            'git_core_lib.git_core_lib.client.git_client.subprocess.run',
            side_effect=OSError('no ps'),
        ):
            self.assertFalse(GitClientMixin._has_running_git_process('/repo'))

    def test_returns_false_when_ps_returns_nonzero(self) -> None:
        # Line 245: ``result.returncode != 0`` → False.
        with patch(
            'git_core_lib.git_core_lib.client.git_client.subprocess.run',
            return_value=_completed(returncode=1, stdout=''),
        ):
            self.assertFalse(GitClientMixin._has_running_git_process('/repo'))

    def test_returns_true_when_ps_line_matches_repo(self) -> None:
        # Lines 251-252: command line contains the ``-C <local_path>`` arg.
        ps_out = 'git -C /repo status\nbash -c something\n'
        with patch(
            'git_core_lib.git_core_lib.client.git_client.subprocess.run',
            return_value=_completed(returncode=0, stdout=ps_out),
        ):
            self.assertTrue(GitClientMixin._has_running_git_process('/repo'))

    def test_returns_false_when_no_matching_repo_arg(self) -> None:
        # The ``git ...`` line exists but it's for a different repo.
        ps_out = 'git -C /other status\n'
        with patch(
            'git_core_lib.git_core_lib.client.git_client.subprocess.run',
            return_value=_completed(returncode=0, stdout=ps_out),
        ):
            self.assertFalse(GitClientMixin._has_running_git_process('/repo'))


class SyncBranchWithRemoteTests(unittest.TestCase):
    def test_raises_when_remote_branch_unavailable_after_fetch(self) -> None:
        # Line 299: ``_git_reference_exists`` returns False after fetch.
        client = _Client()
        with patch.object(client, '_run_git'), \
             patch.object(client, '_git_reference_exists', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'is not available locally'):
                client._sync_branch_with_remote('/repo', 'feat/x')


class RebaseAbortAfterFailureTests(unittest.TestCase):
    def test_logs_warning_when_abort_itself_fails(self) -> None:
        # Lines 329-337: ``rebase --abort`` fails → log warning.
        client = _Client()
        mock_logger = MagicMock()
        client.logger = mock_logger
        with patch.object(client, '_run_git', side_effect=RuntimeError('abort failed')):
            client._abort_rebase_after_failure('/repo', 'feat/x')
        mock_logger.warning.assert_called_once()


class RebaseBranchOntoRemoteTests(unittest.TestCase):
    def test_aborts_and_reraises_when_rebase_fails(self) -> None:
        # Lines 319-321: rebase raises → call _abort_rebase_after_failure + reraise.
        client = _Client()
        with patch.object(client, '_run_git', side_effect=RuntimeError('rebase failed')), \
             patch.object(client, '_abort_rebase_after_failure') as mock_abort:
            with self.assertRaisesRegex(RuntimeError, 'rebase failed'):
                client._rebase_branch_onto_remote('/repo', 'feat/x', 'origin/feat/x')
        mock_abort.assert_called_once()


class UsesHttpRemoteTests(unittest.TestCase):
    def test_https_remote_is_http_remote(self) -> None:
        # Line 365-366: HTTPS counts as HTTP remote (for auth-header check).
        self.assertTrue(GitClientMixin._uses_http_remote('https://github.com/org/r.git'))

    def test_http_remote_is_http_remote(self) -> None:
        self.assertTrue(GitClientMixin._uses_http_remote('http://internal/repo.git'))

    def test_ssh_remote_is_not_http(self) -> None:
        self.assertFalse(GitClientMixin._uses_http_remote('git@github.com:org/r.git'))


class InferDefaultBranchTests(unittest.TestCase):
    def test_strips_refs_remotes_prefix(self) -> None:
        # Line 390: ``output.startswith('refs/remotes/')`` → strip and return tail.
        with patch.object(GitClientMixin, '_validate_git_executable'), \
             patch(
                 'git_core_lib.git_core_lib.client.git_client.subprocess.run',
                 return_value=_completed(returncode=0, stdout='refs/remotes/origin/develop\n'),
             ):
            result = GitClientMixin._infer_default_branch('/repo')
        self.assertEqual(result, 'develop')

    def test_returns_raw_output_when_no_refs_prefix(self) -> None:
        # Bare branch name (from ``branch --show-current``).
        with patch.object(GitClientMixin, '_validate_git_executable'), \
             patch(
                 'git_core_lib.git_core_lib.client.git_client.subprocess.run',
                 return_value=_completed(returncode=0, stdout='main\n'),
             ):
            result = GitClientMixin._infer_default_branch('/repo')
        self.assertEqual(result, 'main')

    def test_raises_when_all_probes_fail(self) -> None:
        # Both probe commands return non-zero or empty → final raise.
        with patch.object(GitClientMixin, '_validate_git_executable'), \
             patch(
                 'git_core_lib.git_core_lib.client.git_client.subprocess.run',
                 return_value=_completed(returncode=1, stdout=''),
             ):
            with self.assertRaisesRegex(ValueError, 'unable to determine'):
                GitClientMixin._infer_default_branch('/repo')


if __name__ == '__main__':
    unittest.main()
