"""Coverage for kato.client.claude.session_history.

The module is tiny but load-bearing: it's how the workspace-recovery
service reattaches an orphan task folder to its existing Claude
conversation, and how the planning UI replays history after kato
restarts. A regression here turns into a silent context loss for the
user, so cover every branch explicitly.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from claude_core_lib.claude_core_lib.session.history import (
    find_session_file,
    find_session_id_for_cwd,
    load_history_events,
)


def _write_jsonl(path: Path, lines: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8') as fh:
        for line in lines:
            fh.write(json.dumps(line) + '\n')


class FindSessionIdForCwdTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.projects_root = Path(self._tmp.name)

    def _seed_session(
        self,
        encoded_dir: str,
        session_id: str,
        cwd: str,
        *,
        mtime: float | None = None,
    ) -> Path:
        path = self.projects_root / encoded_dir / f'{session_id}.jsonl'
        _write_jsonl(path, [
            {'type': 'queue-operation', 'sessionId': session_id},
            {'type': 'user', 'cwd': cwd, 'sessionId': session_id,
             'message': {'role': 'user', 'content': [{'type': 'text', 'text': 'hi'}]}},
        ])
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    def test_returns_empty_on_blank_input(self) -> None:
        self.assertEqual(find_session_id_for_cwd('', projects_root=self.projects_root), '')
        self.assertEqual(find_session_id_for_cwd('   ', projects_root=self.projects_root), '')

    def test_returns_empty_when_projects_root_missing(self) -> None:
        missing = self.projects_root / 'never-created'
        result = find_session_id_for_cwd('/some/repo', projects_root=missing)
        self.assertEqual(result, '')

    def test_returns_empty_when_no_session_matches_cwd(self) -> None:
        self._seed_session(
            'enc-other', 'sess-1', cwd='/Users/shay/different/repo',
        )
        result = find_session_id_for_cwd(
            '/Users/shay/target/repo', projects_root=self.projects_root,
        )
        self.assertEqual(result, '')

    def test_returns_session_id_for_matching_cwd(self) -> None:
        target_cwd = self.projects_root / 'workspaces' / 'PROJ-1' / 'repo'
        target_cwd.mkdir(parents=True)
        self._seed_session(
            'enc-target', 'sess-target', cwd=str(target_cwd),
        )
        result = find_session_id_for_cwd(
            str(target_cwd), projects_root=self.projects_root,
        )
        self.assertEqual(result, 'sess-target')

    def test_picks_most_recent_session_when_multiple_match(self) -> None:
        target_cwd = self.projects_root / 'workspaces' / 'PROJ-1' / 'repo'
        target_cwd.mkdir(parents=True)
        # Older session — same cwd.
        self._seed_session(
            'enc-target', 'sess-old', cwd=str(target_cwd),
            mtime=time.time() - 3600,
        )
        # Newer session — same cwd, fresher mtime.
        self._seed_session(
            'enc-target-newer', 'sess-new', cwd=str(target_cwd),
            mtime=time.time(),
        )

        result = find_session_id_for_cwd(
            str(target_cwd), projects_root=self.projects_root,
        )

        self.assertEqual(result, 'sess-new')

    def test_normalizes_paths_before_comparing(self) -> None:
        # Trailing slash on input shouldn't break the match.
        target_cwd = self.projects_root / 'workspaces' / 'PROJ-1' / 'repo'
        target_cwd.mkdir(parents=True)
        self._seed_session('enc', 'sess-x', cwd=str(target_cwd))

        result = find_session_id_for_cwd(
            str(target_cwd) + '/', projects_root=self.projects_root,
        )

        self.assertEqual(result, 'sess-x')

    def test_skips_sessions_without_cwd_metadata(self) -> None:
        # A JSONL whose first 20 lines are queue-ops with no cwd: must
        # not crash, must not match anything.
        path = self.projects_root / 'enc-noisy' / 'sess-noop.jsonl'
        path.parent.mkdir(parents=True)
        with path.open('w', encoding='utf-8') as fh:
            for _ in range(30):
                fh.write(json.dumps({'type': 'queue-operation'}) + '\n')

        result = find_session_id_for_cwd(
            '/whatever', projects_root=self.projects_root,
        )

        self.assertEqual(result, '')

    def test_skips_jsonl_with_unparseable_first_lines(self) -> None:
        path = self.projects_root / 'enc-bad' / 'sess-bad.jsonl'
        path.parent.mkdir(parents=True)
        path.write_text('not json at all\n', encoding='utf-8')

        # Should not raise — just no match.
        result = find_session_id_for_cwd(
            '/whatever', projects_root=self.projects_root,
        )
        self.assertEqual(result, '')


class FindSessionFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.projects_root = Path(self._tmp.name)

    def test_returns_none_for_blank_session_id(self) -> None:
        self.assertIsNone(find_session_file('', projects_root=self.projects_root))

    def test_returns_none_when_no_jsonl_matches(self) -> None:
        result = find_session_file('missing-id', projects_root=self.projects_root)
        self.assertIsNone(result)

    def test_finds_jsonl_under_any_encoded_project_dir(self) -> None:
        target = self.projects_root / 'enc-x' / 'session-id.jsonl'
        target.parent.mkdir(parents=True)
        target.write_text('{}\n', encoding='utf-8')

        result = find_session_file('session-id', projects_root=self.projects_root)

        self.assertEqual(result, target)


class LoadHistoryEventsTests(unittest.TestCase):
    """Existing replay logic gets light coverage here for safety."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.projects_root = Path(self._tmp.name)

    def test_returns_empty_when_session_not_found(self) -> None:
        events = load_history_events('missing', projects_root=self.projects_root)
        self.assertEqual(events, [])

    def test_filters_internal_noise_keeps_user_assistant(self) -> None:
        path = self.projects_root / 'enc-x' / 'sess-1.jsonl'
        path.parent.mkdir(parents=True)
        _write_jsonl(path, [
            {'type': 'queue-operation'},
            {'type': 'attachment'},
            {
                'type': 'user',
                'message': {'role': 'user', 'content': [{'type': 'text', 'text': 'hello'}]},
            },
            {
                'type': 'assistant',
                'message': {
                    'role': 'assistant',
                    'content': [{'type': 'text', 'text': 'hi back'}],
                },
            },
        ])

        events = load_history_events('sess-1', projects_root=self.projects_root)

        types = [event['type'] for event in events]
        self.assertEqual(types, ['user', 'assistant'])


if __name__ == '__main__':
    unittest.main()
