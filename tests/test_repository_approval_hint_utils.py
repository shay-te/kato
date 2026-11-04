"""How to approve a repository, said the same way everywhere.

A new user's first refused task used to tell them to run ``./kato approve-repo``
(a command that was removed) or to open "Settings → Repositories" (a tab with
no approvals in it). The words now come from one helper, and a gate keeps the
dead command from coming back.
"""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

from kato_core_lib.helpers.repository_approval_hint_utils import (
    APPROVALS_LOCATION,
    approve_repositories_hint,
    not_approved_text,
    repository_list_text,
    restricted_mode_refusal_text,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


class WordingTests(unittest.TestCase):
    def test_names_are_listed_the_way_a_person_reads_them(self) -> None:
        self.assertEqual(repository_list_text([]), '')
        self.assertEqual(repository_list_text(['api', ' ', '']), 'api')
        self.assertEqual(repository_list_text(['api', 'web']), 'api and web')
        self.assertEqual(repository_list_text(['api', 'web', 'lib']), 'api, web and lib')

    def test_the_fix_names_the_approvals_tab(self) -> None:
        self.assertEqual(APPROVALS_LOCATION, 'Settings → Approvals')
        self.assertEqual(approve_repositories_hint(['api']), 'Approve api in Settings → Approvals, then try again.')
        self.assertIn('the repository', approve_repositories_hint([]))

    def test_one_or_several_repositories_read_right(self) -> None:
        self.assertEqual(
            not_approved_text(['api']),
            'api is not approved for agent work yet. Approve api in Settings → Approvals, then try again.',
        )
        self.assertTrue(not_approved_text(['api', 'web']).startswith('api and web are not approved'))
        self.assertTrue(not_approved_text([]).startswith('a repository is not approved'))

    def test_a_restricted_refusal_says_what_is_missing_and_offers_trusted(self) -> None:
        one = restricted_mode_refusal_text(['vendor'], ['the Docker sandbox turned on (here)'])
        self.assertIn('vendor is approved in restricted mode, which also needs the Docker sandbox', one)
        self.assertIn('Switch it to trusted in Settings → Approvals', one)
        several = restricted_mode_refusal_text(['a', 'b'], [])
        self.assertIn('a and b are approved', several)
        self.assertIn('a stricter setup', several)
        self.assertIn('Switch them to trusted', several)


class RemovedCommandGateTests(unittest.TestCase):
    def test_nothing_tells_anyone_to_run_the_removed_approve_repo_command(self) -> None:
        found = subprocess.run(
            ['git', 'grep', '-n', 'approve-repo', '--',
             '*.py', '*.js', '*.jsx', '*.md', ':!*test*', ':!webserver/static/build/*'],
            cwd=str(REPO_ROOT), capture_output=True, text=True, stdin=subprocess.DEVNULL,
        )
        self.assertEqual(found.stdout.strip(), '', 'the approve-repo command is gone; point at Settings → Approvals')


if __name__ == '__main__':
    unittest.main()
