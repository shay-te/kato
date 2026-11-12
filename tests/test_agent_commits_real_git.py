"""Strict git off, end to end on real repositories: kato publishing the agent's own git.

With local git access the agent commits, amends and rebases in the task clone
itself. Kato's publish path was written when only kato committed; these drive
the real ``RepositoryService`` against a real bare origin and a real clone, with
the agent's side played by plain ``git`` in the clone, exactly as the agent's
Bash would run it:

* the agent's own commits are pushed as they are — kato adds no commit of its
  own over a clean tree, and still commits anything left uncommitted;
* commits the agent rewrote after they were pushed are refused, not rebased
  onto themselves;
* a rebase the agent is in the middle of is left alone by every kato path
  that would otherwise move the clone (self-heal, branch prep, publish).
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from kato_core_lib.data_layers.service.repository_service import RepositoryService
from tests.test_repository_service_real_git import _git, _make_bare_origin, _make_repository

BRANCH = 'PROJ-7'


@unittest.skipUnless(shutil.which('git'), 'git binary not available on this system')
class _TaskClone(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix='agent-commits-')
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.origin = _make_bare_origin(root)
        self.clone = root / 'workspaces' / BRANCH / 'api'
        self.clone.parent.mkdir(parents=True)
        _git(root, 'clone', str(self.origin), str(self.clone))
        _git(self.clone, 'checkout', '-b', BRANCH)
        self.repository = _make_repository(
            repo_id='api', local_path=self.clone, remote_url=self.origin,
        )
        self.service = RepositoryService([self.repository], 1)

    def _agent_commits(self, name: str, text: str, message: str) -> None:
        (self.clone / name).write_text(text, encoding='utf-8')
        _git(self.clone, 'add', name)
        _git(self.clone, 'commit', '-m', message)

    def _kato_publishes(self, message: str = 'kato: PROJ-7') -> None:
        self.service._publish_branch_updates(
            str(self.clone), BRANCH, 'main', message, self.repository,
            restore_workspace=False,
        )

    def _origin_log(self) -> list[str]:
        return _git(self.origin, 'log', '--format=%s', BRANCH).stdout.split('\n')[:-1]


class AgentCommitsArePublishedTests(_TaskClone):
    def test_the_agents_commit_is_pushed_as_it_is(self) -> None:
        self._agent_commits('parser.py', 'fixed\n', 'Fix the parser on empty input')

        self._kato_publishes()

        self.assertEqual(self._origin_log(), ['Fix the parser on empty input', 'seed'])

    def test_what_the_agent_left_uncommitted_is_still_committed_by_kato(self) -> None:
        self._agent_commits('parser.py', 'fixed\n', 'Fix the parser')
        (self.clone / 'notes.md').write_text('left over\n', encoding='utf-8')

        self._kato_publishes('kato: the rest')

        self.assertEqual(self._origin_log(), ['kato: the rest', 'Fix the parser', 'seed'])

    def test_a_squash_before_the_first_push_is_just_history(self) -> None:
        self._agent_commits('a.py', 'a\n', 'wip 1')
        self._agent_commits('b.py', 'b\n', 'wip 2')
        _git(self.clone, 'reset', '--soft', 'HEAD~2')
        _git(self.clone, 'commit', '-m', 'Add a and b')

        self._kato_publishes()

        self.assertEqual(self._origin_log(), ['Add a and b', 'seed'])


class RewrittenAfterPushTests(_TaskClone):
    def setUp(self) -> None:
        super().setUp()
        self._agent_commits('parser.py', 'fixed\n', 'Fix the parser')
        self._kato_publishes()

    def test_an_amend_after_the_push_is_refused_and_says_why(self) -> None:
        _git(self.clone, 'commit', '--amend', '-m', 'Fix the parser, reworded')

        with self.assertRaisesRegex(RuntimeError, 'rewritten after the last push'):
            self._kato_publishes()

        self.assertEqual(self._origin_log(), ['Fix the parser', 'seed'])
        self.assertEqual(
            _git(self.clone, 'log', '--format=%s', '-1').stdout.strip(),
            'Fix the parser, reworded',
            'the refused push must not have rebased the clone either',
        )

    def test_new_commits_after_the_push_go_out_normally(self) -> None:
        self._agent_commits('lexer.py', 'also\n', 'Fix the lexer')

        self._kato_publishes()

        self.assertEqual(self._origin_log(), ['Fix the lexer', 'Fix the parser', 'seed'])


class RebaseInProgressTests(_TaskClone):
    """The agent's rebase stopped on a conflict; HEAD is detached until it continues."""

    def setUp(self) -> None:
        super().setUp()
        self._agent_commits('README.md', 'branch\n', 'on the branch')
        _git(self.clone, 'checkout', 'main')
        self._agent_commits('README.md', 'main\n', 'on main')
        _git(self.clone, 'checkout', BRANCH)
        _git(self.clone, 'rebase', 'main', check=False)
        self.assertEqual(self.service.git_operation_in_progress(str(self.clone)), 'rebase')

    def _still_mid_rebase(self) -> None:
        self.assertTrue((self.clone / '.git' / 'rebase-merge').is_dir())
        self.assertIn('<<<<<<<', (self.clone / 'README.md').read_text(encoding='utf-8'))

    def test_the_self_heal_leaves_it_alone(self) -> None:
        reason = self.service.recover_clone_onto_task_branch(self.repository, BRANCH)

        self.assertIn('git rebase is in progress', reason)
        self.assertIn('git rebase --continue', reason)
        self._still_mid_rebase()

    def test_branch_prep_refuses_instead_of_wiping(self) -> None:
        with self.assertRaisesRegex(RuntimeError, 'git rebase is in progress'):
            self.service._prepare_workspace_for_branch(
                str(self.clone), 'main', BRANCH, self.repository,
            )
        self._still_mid_rebase()

    def test_publish_says_a_rebase_is_in_progress(self) -> None:
        with self.assertRaisesRegex(RuntimeError, 'git rebase is in progress'):
            self._kato_publishes()
        self._still_mid_rebase()
        self.assertNotIn(BRANCH, _git(self.origin, 'branch', '--list').stdout)


if __name__ == '__main__':
    unittest.main()
