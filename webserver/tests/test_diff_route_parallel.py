"""``/api/sessions/<task>/diff`` builds every repository at once, in order.

It ran one repository after another on every 5-second poll: a six-repo task
spent 1.5 s per poll in this route (6.6 s measured with the slower git wrapper).
The repositories are independent, so they are built concurrently — while the
response keeps the order the task lists them in.

A barrier that every repository's build must reach before any may finish proves
the builds overlap: run one at a time, the first would wait forever. Fakes are
local, like every other file here.
"""
from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from kato_webserver import app as app_module
from kato_webserver.app import create_app

REPOS = ['ob-love-admin-client', 'ob-love-admin-backend', 'core-lib']


class _Workspace:
    def __init__(self, root: Path) -> None:
        self._root = root
        self.record = type('Record', (), {'task_id': 'T-1', 'repository_ids': REPOS, 'status': 'active'})()

    def list_workspaces(self):
        return [self.record]

    def get(self, task_id):
        return self.record if task_id == 'T-1' else None

    def repository_path(self, task_id, repo_id):  # noqa: ARG002
        return self._root / repo_id

    def workspace_path(self, task_id):  # noqa: ARG002
        return self._root


class _Manager:
    def list_records(self):
        return []

    def get_record(self, task_id):  # noqa: ARG002
        return None

    def get_session(self, task_id):  # noqa: ARG002
        return None


class DiffRouteBuildsReposInParallelTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        for repo in REPOS:
            (root / repo / '.git').mkdir(parents=True)
        self.app = create_app(session_manager=_Manager(), workspace_manager=_Workspace(root))

    def _get(self):
        return self.app.test_client().get('/api/sessions/T-1/diff')

    def test_every_repository_is_built_at_the_same_time(self) -> None:
        barrier = threading.Barrier(len(REPOS), timeout=5)

        def compute(repo_id, cwd, **_kwargs):
            barrier.wait()  # one at a time, the first build never gets past this
            return {'repo_id': repo_id, 'cwd': cwd, 'base': 'master', 'head': 'T-1',
                    'diff': f'diff of {repo_id}', 'conflicted_files': [], 'error': ''}

        with patch.object(app_module, '_compute_repo_diff', side_effect=compute):
            response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual([d['repo_id'] for d in response.get_json()['diffs']], REPOS)

    def test_the_task_order_is_kept_whichever_repository_finishes_first(self) -> None:
        delays = {REPOS[0]: 0.15, REPOS[1]: 0.0, REPOS[2]: 0.05}

        def compute(repo_id, cwd, **_kwargs):
            threading.Event().wait(delays[repo_id])
            return {'repo_id': repo_id, 'cwd': cwd, 'base': '', 'head': '', 'diff': '',
                    'conflicted_files': [], 'error': ''}

        with patch.object(app_module, '_compute_repo_diff', side_effect=compute):
            body = self._get().get_json()
        self.assertEqual(body['repository_ids'], REPOS)

    def test_the_first_repository_is_not_repeated_at_the_top_level(self) -> None:
        def compute(repo_id, cwd, **_kwargs):
            return {'repo_id': repo_id, 'cwd': cwd, 'base': 'master', 'head': 'T-1',
                    'diff': 'x' * 1000, 'conflicted_files': [], 'error': ''}

        with patch.object(app_module, '_compute_repo_diff', side_effect=compute):
            body = self._get().get_json()
        for duplicate in ('diff', 'base', 'head', 'repo_id'):
            self.assertNotIn(duplicate, body)
        self.assertEqual(len(body['diffs']), len(REPOS))


if __name__ == '__main__':
    unittest.main()
