"""kato must not open a pull request with nothing in it.

Reported after declining five of them. Two ways it happened, both here:

  * the agent made a change and then reverted it, so the branch's commits
    cancel out and the diff against the destination is empty;
  * a multi-repo task where one repo has the fix and the others were already
    merged — publishing re-opened a PR for every repo, and the operator
    declined the empty ones by hand, once per repo, every time.

The test is the DIFF against the destination, not the commit count: a revert
leaves commits behind but no net change, and counting commits calls that
publishable.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from kato_core_lib.data_layers.service.repository_service import (
    RepositoryService,
)


def _git(cwd, *args):
    subprocess.run(['git', *args], cwd=str(cwd), check=True,
                   capture_output=True, text=True)


def _commit(repo: Path, name: str, body: str) -> None:
    (repo / name).write_text(body)
    _git(repo, 'add', '-A')
    _git(repo, 'commit', '-qm', f'touch {name}')


class EmptyPullRequestGuardTests(unittest.TestCase):
    """Real git: a fixture cannot fake a diff."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix='kato-empty-pr-')
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name) / 'clone'
        self.repo.mkdir()
        _git(self.repo, 'init', '-q', '-b', 'main')
        _git(self.repo, 'config', 'user.email', 't@example.com')
        _git(self.repo, 'config', 'user.name', 'T')
        _commit(self.repo, 'base.txt', 'base\n')

        self.service = RepositoryService.__new__(RepositoryService)
        self.service._validate_git_executable = MagicMock()
        self.service.destination_branch = MagicMock(return_value='main')
        self.service._run_git = self._run_git

    def _run_git(self, cwd, args, _message='', _repository=None):
        result = subprocess.run(
            ['git', *args], cwd=str(cwd), capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr or 'git failed')
        return result.stdout

    def _repository(self):
        return SimpleNamespace(id='repo', local_path=str(self.repo))

    def _reason(self, branch='task/UNA-1'):
        return self.service.pull_request_skip_reason(self._repository(), branch)

    def test_a_branch_with_real_changes_is_publishable(self) -> None:
        _git(self.repo, 'checkout', '-qb', 'task/UNA-1')
        _commit(self.repo, 'feature.txt', 'work\n')
        self.assertEqual(self._reason(), '')

    def test_a_branch_whose_commits_CANCEL_OUT_is_refused(self) -> None:
        """The reported case: changed, then reverted."""
        _git(self.repo, 'checkout', '-qb', 'task/UNA-1')
        _commit(self.repo, 'feature.txt', 'work\n')
        _git(self.repo, 'rm', '-q', 'feature.txt')
        _git(self.repo, 'commit', '-qm', 'revert it')
        reason = self._reason()
        self.assertIn('would be empty', reason)

    def test_a_branch_with_NO_commits_is_refused(self) -> None:
        """The already-merged repo in a multi-repo task."""
        _git(self.repo, 'checkout', '-qb', 'task/UNA-1')
        self.assertIn('would be empty', self._reason())

    def test_the_reason_names_the_branch_and_base(self) -> None:
        _git(self.repo, 'checkout', '-qb', 'task/UNA-1')
        reason = self._reason()
        self.assertIn('task/UNA-1', reason)
        self.assertIn('main', reason)

    def test_destination_commits_do_not_count_as_the_branch_s_work(self) -> None:
        # Three-dot: main moving ahead is not the branch having changes.
        _git(self.repo, 'checkout', '-qb', 'task/UNA-1')
        _git(self.repo, 'checkout', '-q', 'main')
        _commit(self.repo, 'other.txt', 'someone else\n')
        _git(self.repo, 'checkout', '-q', 'task/UNA-1')
        self.assertIn('would be empty', self._reason())

    def test_it_never_blocks_when_git_cannot_answer(self) -> None:
        # Safe direction: a missed empty PR is an annoyance, a suppressed
        # real one loses work.
        self.service.destination_branch = MagicMock(side_effect=ValueError())
        _git(self.repo, 'checkout', '-qb', 'task/UNA-1')
        self.assertEqual(self._reason(), '')

    def test_an_unknown_workspace_never_blocks(self) -> None:
        service = RepositoryService.__new__(RepositoryService)
        service._validate_git_executable = MagicMock()
        repository = SimpleNamespace(id='r', local_path='/nope')
        self.assertEqual(
            service.pull_request_skip_reason(repository, 'task/UNA-1'), '',
        )

    def test_no_branch_name_never_blocks(self) -> None:
        self.assertEqual(self._reason(''), '')


class PublishPathsHonourTheGuardTests(unittest.TestCase):
    """Both publish paths consult it, and neither breaks without it."""

    def _publish_service(self, reason):
        from kato_core_lib.data_layers.service.task_publish_service import (
            TaskPublishService,
        )
        repository_service = MagicMock()
        repository_service.pull_request_skip_reason.return_value = reason
        return TaskPublishService(
            repository_service=repository_service,
            task_service=MagicMock(),
            task_state_service=MagicMock(),
            task_publisher=MagicMock(),
            workspace_manager=MagicMock(),
            logger=MagicMock(),
        )

    def test_the_on_demand_path_reports_no_reason_as_publishable(self) -> None:
        service = self._publish_service('')
        self.assertEqual(
            service._empty_pull_request_reason(SimpleNamespace(id='r'), 'b'), '',
        )

    def test_the_on_demand_path_surfaces_the_reason(self) -> None:
        service = self._publish_service('would be empty')
        self.assertEqual(
            service._empty_pull_request_reason(SimpleNamespace(id='r'), 'b'),
            'would be empty',
        )

    def test_a_service_without_the_check_still_publishes(self) -> None:
        from kato_core_lib.data_layers.service.task_publish_service import (
            TaskPublishService,
        )
        service = TaskPublishService(
            repository_service=MagicMock(spec=[]),
            task_service=MagicMock(),
            task_state_service=MagicMock(),
            task_publisher=MagicMock(),
            workspace_manager=MagicMock(),
            logger=MagicMock(),
        )
        self.assertEqual(
            service._empty_pull_request_reason(SimpleNamespace(id='r'), 'b'), '',
        )

    def test_a_check_that_raises_still_publishes(self) -> None:
        service = self._publish_service('')
        service._repository_service.pull_request_skip_reason.side_effect = (
            RuntimeError('git down')
        )
        self.assertEqual(
            service._empty_pull_request_reason(SimpleNamespace(id='r'), 'b'), '',
        )


if __name__ == '__main__':
    unittest.main()


class OnlyARealReasonBlocksAPublishTests(unittest.TestCase):
    """A non-string answer must never suppress a pull request.

    The probe is looked up by name on whatever repository service is wired.
    A stub, a mock, or a service that answers with something other than a
    string would read as truthy and silently suppress EVERY pull request —
    losing work, which is the one outcome this guard must never cause.
    """

    def _service(self, answer):
        from kato_core_lib.data_layers.service.task_publish_service import (
            TaskPublishService,
        )
        repository_service = MagicMock()
        repository_service.pull_request_skip_reason.return_value = answer
        return TaskPublishService(
            repository_service=repository_service,
            task_service=MagicMock(),
            task_state_service=MagicMock(),
            task_publisher=MagicMock(),
            workspace_manager=MagicMock(),
            logger=MagicMock(),
        )

    def _reason(self, answer):
        return self._service(answer)._empty_pull_request_reason(
            SimpleNamespace(id='r'), 'branch',
        )

    def test_a_real_reason_blocks(self) -> None:
        self.assertEqual(self._reason('would be empty'), 'would be empty')

    def test_a_mock_does_NOT_block(self) -> None:
        self.assertEqual(self._reason(MagicMock()), '')

    def test_a_bare_true_does_NOT_block(self) -> None:
        self.assertEqual(self._reason(True), '')

    def test_none_does_not_block(self) -> None:
        self.assertEqual(self._reason(None), '')

    def test_whitespace_does_not_block(self) -> None:
        self.assertEqual(self._reason('   '), '')
