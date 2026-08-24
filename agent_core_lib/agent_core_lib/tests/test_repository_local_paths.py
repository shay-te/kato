"""The workspace paths every agent prompt scopes the agent to.

Both CLI transports had their own copy of this walk, which is one more way the
two prompts could quietly stop matching. A blank path is dropped rather than
emitted: an empty bullet in the scope block reads to the model as a boundary
it cannot check.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from agent_core_lib.agent_core_lib.helpers.agent_prompt_utils import (
    repository_local_paths,
)


def _repo(local_path: str) -> SimpleNamespace:
    return SimpleNamespace(id='r', local_path=local_path)


class RepositoryLocalPathsTests(unittest.TestCase):
    def test_returns_each_clone_path_in_order(self) -> None:
        prepared = SimpleNamespace(repositories=[_repo('/wks/api'), _repo('/wks/ui')])

        self.assertEqual(repository_local_paths(prepared), ['/wks/api', '/wks/ui'])

    def test_blank_and_whitespace_paths_are_skipped(self) -> None:
        prepared = SimpleNamespace(repositories=[
            _repo('   '), _repo(''), _repo('/wks/PROJ-1/api'),
        ])

        self.assertEqual(repository_local_paths(prepared), ['/wks/PROJ-1/api'])

    def test_a_missing_or_none_path_is_skipped(self) -> None:
        prepared = SimpleNamespace(repositories=[
            SimpleNamespace(id='no-attr'),
            SimpleNamespace(id='none', local_path=None),
            _repo('/wks/api'),
        ])

        self.assertEqual(repository_local_paths(prepared), ['/wks/api'])

    def test_paths_are_stripped(self) -> None:
        prepared = SimpleNamespace(repositories=[_repo('  /wks/api  ')])

        self.assertEqual(repository_local_paths(prepared), ['/wks/api'])

    def test_no_prepared_task_and_no_repositories_are_both_empty(self) -> None:
        self.assertEqual(repository_local_paths(None), [])
        self.assertEqual(repository_local_paths(SimpleNamespace(repositories=[])), [])
        self.assertEqual(repository_local_paths(SimpleNamespace()), [])
        self.assertEqual(repository_local_paths(SimpleNamespace(repositories=None)), [])


if __name__ == '__main__':
    unittest.main()
