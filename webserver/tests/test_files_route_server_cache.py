"""``/api/sessions/<task>/files`` answered from the server's tree cache.

The operator: "can the repo loading and caching happen in the backend please?"
A client with nothing on screen asks with ``?cached=1`` and must get the last tree
the server built without any git work. Every other read builds fresh and
refreshes that copy. The copy survives a restart when a directory is configured,
and goes when the task is forgotten.

A real Flask app. The per-repo git passes are replaced with a counter, so each
test can prove whether a build happened at all. The fakes are local on purpose,
like every other file here, so this one runs on its own.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from kato_webserver.app import create_app

TASK_ID = 'CACHE-TEST-1'


class _Record:
    def __init__(self, task_id: str, cwd: str) -> None:
        self.task_id = task_id
        self.cwd = cwd

    def to_dict(self):
        return {'task_id': self.task_id, 'cwd': self.cwd}


class _SessionManager:
    def __init__(self, records) -> None:
        self._records = list(records)

    def list_records(self):
        return list(self._records)

    def get_record(self, task_id):
        return next((r for r in self._records if r.task_id == task_id), None)

    def get_session(self, task_id):  # noqa: ARG002
        return None

    def terminate_session(self, task_id, *, remove_record=False):  # noqa: ARG002
        return None


class _WorkspaceManager:
    """No repository clones — so the files route reads the session record's
    directory — and a delete that succeeds."""

    def list_workspaces(self):
        return []

    def get(self, task_id):  # noqa: ARG002
        return None

    def repository_path(self, task_id, repo_id):  # noqa: ARG002
        return Path('/missing')

    def workspace_path(self, task_id):  # noqa: ARG002
        return Path('/missing')

    def delete(self, task_id):  # noqa: ARG002
        return None


class FilesRouteServerCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.repo = root / 'repo'
        (self.repo / '.git').mkdir(parents=True)
        self.cache_dir = root / 'cache'
        # Whatever the routes record under the operator's home (the forget route
        # writes several stores) lands in the temp dir, never the real ~/.kato.
        home = patch.dict(os.environ, {'HOME': str(root / 'home')})
        home.start()
        self.addCleanup(home.stop)
        self.files = ['README.md']
        self.builds = 0
        for target, kwargs in (
            ('kato_webserver.app.tracked_file_tree', {'side_effect': self._tree}),
            ('kato_webserver.app.conflicted_paths', {'return_value': []}),
            ('kato_webserver.app._changed_files_for_repo', {'return_value': []}),
        ):
            patcher = patch(target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _tree(self, cwd):  # noqa: ARG002
        self.builds += 1
        return [{'name': name, 'kind': 'file'} for name in self.files]

    def _app(self, *, cache_dir=None, agent_service=None):
        return create_app(
            session_manager=_SessionManager([_Record(TASK_ID, str(self.repo))]),
            workspace_manager=_WorkspaceManager(),
            agent_service=agent_service,
            file_tree_cache_dir=str(cache_dir) if cache_dir else '',
        )

    def _get(self, app, query: str = ''):
        response = app.test_client().get(f'/api/sessions/{TASK_ID}/files{query}')
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    @staticmethod
    def _names(payload):
        return [entry['name'] for entry in payload['trees'][0]['tree']]

    def test_with_nothing_cached_a_cached_request_builds_like_any_read(self) -> None:
        payload = self._get(self._app(), '?cached=1')
        self.assertEqual(self.builds, 1)
        self.assertNotIn('cache_hit', payload)
        self.assertEqual(self._names(payload), ['README.md'])

    def test_a_cached_request_is_answered_without_any_git_work(self) -> None:
        app = self._app()
        self._get(app)
        self.files = ['README.md', 'added-since.py']
        payload = self._get(app, '?cached=1')
        self.assertEqual(self.builds, 1)
        self.assertTrue(payload['cache_hit'])
        self.assertEqual(self._names(payload), ['README.md'])

    def test_a_plain_request_always_builds_fresh_and_refreshes_the_copy(self) -> None:
        app = self._app()
        self._get(app)
        self.files = ['README.md', 'added-since.py']
        self.assertEqual(self._names(self._get(app)), ['README.md', 'added-since.py'])
        self.assertEqual(self.builds, 2)
        cached = self._get(app, '?cached=1')
        self.assertEqual(self._names(cached), ['README.md', 'added-since.py'])
        self.assertEqual(self.builds, 2)

    def test_the_copy_survives_a_restart_when_a_directory_is_configured(self) -> None:
        self._get(self._app(cache_dir=self.cache_dir))
        payload = self._get(self._app(cache_dir=self.cache_dir), '?cached=1')
        self.assertTrue(payload['cache_hit'])
        self.assertEqual(self.builds, 1)

    def test_an_app_without_a_directory_writes_nothing_to_disk(self) -> None:
        self._get(self._app())
        self.assertFalse(self.cache_dir.exists())
        self._get(self._app(), '?cached=1')
        self.assertEqual(self.builds, 2)

    def test_a_cached_answer_never_finalises_a_merge(self) -> None:
        finalised: list[str] = []
        agent_service = SimpleNamespace(publish=SimpleNamespace(
            finalize_resolved_merges_for_task=finalised.append,
        ))
        app = self._app(agent_service=agent_service)
        self._get(app)
        self._get(app, '?cached=1')
        self.assertEqual(finalised, [TASK_ID])

    def test_forgetting_the_task_drops_its_cached_tree(self) -> None:
        app = self._app(cache_dir=self.cache_dir)
        self._get(app)
        self.assertEqual(len(list(self.cache_dir.glob('*.json'))), 1)
        response = app.test_client().delete(f'/api/sessions/{TASK_ID}/workspace')
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(list(self.cache_dir.glob('*.json')), [])
        payload = self._get(app, '?cached=1')
        self.assertNotIn('cache_hit', payload)
        self.assertEqual(self.builds, 2)


if __name__ == '__main__':
    unittest.main()
