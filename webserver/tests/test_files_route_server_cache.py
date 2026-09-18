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

import gzip
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from kato_webserver.app import create_app
from kato_webserver.file_tree_cache import CACHE_HIT_HEADER, CACHE_HIT_VALUE

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


class _FilesRouteHarness(unittest.TestCase):
    """Fakes and helpers shared by the route's test classes; no tests itself."""

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

    def _fetch(self, app, query: str = '', **kwargs):
        response = app.test_client().get(
            f'/api/sessions/{TASK_ID}/files{query}', **kwargs,
        )
        self.assertIn(response.status_code, (200, 304))
        return response

    def _get(self, app, query: str = ''):
        response = self._fetch(app, query)
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    @staticmethod
    def _names(payload):
        return [entry['name'] for entry in payload['trees'][0]['tree']]

    @staticmethod
    def _served_from_cache(response) -> bool:
        return response.headers.get(CACHE_HIT_HEADER) == CACHE_HIT_VALUE


class FilesRouteServerCacheTests(_FilesRouteHarness):
    """The server's copy of the tree: stored, served, refreshed, forgotten."""

    def test_with_nothing_cached_a_cached_request_builds_like_any_read(self) -> None:
        response = self._fetch(self._app(), '?cached=1')
        self.assertEqual(self.builds, 1)
        self.assertFalse(self._served_from_cache(response))
        self.assertEqual(self._names(response.get_json()), ['README.md'])

    def test_a_cached_request_is_answered_without_any_git_work(self) -> None:
        app = self._app()
        self._get(app)
        self.files = ['README.md', 'added-since.py']
        response = self._fetch(app, '?cached=1')
        self.assertEqual(self.builds, 1)
        self.assertTrue(self._served_from_cache(response))
        self.assertEqual(self._names(response.get_json()), ['README.md'])

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
        response = self._fetch(self._app(cache_dir=self.cache_dir), '?cached=1')
        self.assertTrue(self._served_from_cache(response))
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
        rebuilt = self._fetch(app, '?cached=1')
        self.assertFalse(self._served_from_cache(rebuilt))
        self.assertEqual(self.builds, 2)


class FilesRouteUnchangedTreeTests(_FilesRouteHarness):
    """An unchanged tree is confirmed, not re-sent.

    The Files pane re-reads the tree every few seconds while a task is open,
    and it is almost always byte-identical to the last one — the agent edits a
    handful of files, it does not reshape the repository. On a 27-repository
    task that was 1.4 MB downloaded and parsed every five seconds to be thrown
    away as unchanged.
    """

    def _etag(self, app) -> str:
        return self._fetch(app).headers['ETag']

    def test_a_tree_is_served_with_an_etag(self) -> None:
        self.assertTrue(self._fetch(self._app()).headers.get('ETag'))

    def test_the_same_tree_keeps_the_same_etag(self) -> None:
        app = self._app()
        first = self._etag(app)
        rebuilt = self._etag(app)
        self.assertEqual(first, rebuilt)

    def test_a_changed_tree_gets_a_new_etag(self) -> None:
        app = self._app()
        before = self._etag(app)
        self.files = ['README.md', 'added-since.py']
        self.assertNotEqual(before, self._etag(app))

    def test_an_unchanged_tree_comes_back_as_an_empty_304(self) -> None:
        app = self._app()
        etag = self._etag(app)
        response = self._fetch(app, headers={'If-None-Match': etag})
        self.assertEqual(response.status_code, 304)
        self.assertEqual(response.get_data(), b'')

    def test_a_304_still_rebuilds_so_a_real_change_is_never_missed(self) -> None:
        # The tag is compared against a FRESH build, never used to skip one:
        # the cache must not become a second opinion on what is current.
        app = self._app()
        etag = self._etag(app)
        self.files = ['README.md', 'added-since.py']
        response = self._fetch(app, headers={'If-None-Match': etag})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self._names(response.get_json()), ['README.md', 'added-since.py'],
        )

    def test_a_cached_answer_is_taggable_too(self) -> None:
        # The cached body is byte-identical to the fresh build of the same
        # tree, so the follow-up request the client makes right after painting
        # it comes back as a 304 — the point of not marking the body itself.
        app = self._app()
        self._get(app)
        cached = self._fetch(app, '?cached=1')
        self.assertTrue(self._served_from_cache(cached))
        follow_up = self._fetch(app, headers={'If-None-Match': cached.headers['ETag']})
        self.assertEqual(follow_up.status_code, 304)

    def test_a_stale_tag_is_answered_with_the_whole_tree(self) -> None:
        response = self._fetch(
            self._app(), headers={'If-None-Match': '"not-the-current-tree"'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._names(response.get_json()), ['README.md'])

    def test_a_client_that_accepts_gzip_is_sent_gzip(self) -> None:
        response = self._fetch(
            self._app(), headers={'Accept-Encoding': 'gzip'},
        )
        self.assertEqual(response.headers.get('Content-Encoding'), 'gzip')
        self.assertEqual(response.headers.get('Vary'), 'Accept-Encoding')
        # werkzeug's test client leaves the body encoded; it must decompress to
        # exactly the tree, or the browser shows nothing at all.
        self.assertEqual(
            self._names(json.loads(gzip.decompress(response.get_data()))),
            ['README.md'],
        )

    def test_a_client_that_does_not_accept_gzip_gets_plain_json(self) -> None:
        response = self._fetch(self._app(), headers={'Accept-Encoding': 'identity'})
        self.assertIsNone(response.headers.get('Content-Encoding'))
        self.assertEqual(self._names(response.get_json()), ['README.md'])


if __name__ == '__main__':
    unittest.main()
