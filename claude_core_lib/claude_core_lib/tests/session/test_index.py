"""Tests for Claude Code session adoption in the planning UI.

Two surfaces are pinned down here:

1. ``ClaudeSessionMetadata`` discovery — kato walks
   ``~/.claude/projects/`` (or ``KATO_CLAUDE_SESSIONS_ROOT`` for
   tests), parses the JSONL transcripts, and returns metadata the
   planning UI dropdown can render. Search filtering, recency
   ordering, malformed-line tolerance, and bounded read are all
   nailed down.
2. ``ClaudeSessionManager.adopt_session_id`` — when the operator
   picks a session, kato writes the session id into the per-task
   record so the next agent spawn ``--resume``s that conversation
   instead of starting fresh. Idempotent, refuses empty ids,
   creates a record from scratch when none existed before.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from claude_core_lib.claude_core_lib.session.manager import (
    ClaudeSessionManager,
    PlanningSessionRecord,
    SESSION_STATUS_TERMINATED,
)
from claude_core_lib.claude_core_lib.session.index import (
    CLAUDE_SESSIONS_ROOT_ENV_KEY,
    ClaudeSessionMetadata,
    claude_project_dir_for_cwd,
    default_sessions_root,
    list_sessions,
    migrate_session_to_workspace,
)


def _write_transcript(
    root: Path,
    project_dir: str,
    session_id: str,
    *,
    cwd: str = '/Users/dev/repos/myproj',
    user_messages: list[str] | None = None,
    extra_lines: list[dict] | None = None,
    file_mtime: float | None = None,
) -> Path:
    project_path = root / project_dir
    project_path.mkdir(parents=True, exist_ok=True)
    transcript_path = project_path / f'{session_id}.jsonl'
    lines: list[str] = []
    for text in user_messages or []:
        lines.append(json.dumps({
            'type': 'user',
            'sessionId': session_id,
            'cwd': cwd,
            'message': {'content': text},
        }))
    for raw in extra_lines or []:
        lines.append(json.dumps(raw))
    transcript_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    if file_mtime is not None:
        os.utime(transcript_path, (file_mtime, file_mtime))
    return transcript_path


class SessionDiscoveryTests(unittest.TestCase):
    """Walking the JSONL store, parsing, ordering."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_returns_empty_when_root_missing(self) -> None:
        self.assertEqual(
            list_sessions(sessions_root=self.root / 'does-not-exist'),
            [],
        )

    def test_returns_empty_when_no_transcripts(self) -> None:
        # Empty projects dir → empty list, not a crash.
        self.assertEqual(list_sessions(sessions_root=self.root), [])

    def test_discovers_single_session(self) -> None:
        _write_transcript(
            self.root, '-Users-dev-repos-myproj', 'sess-1',
            user_messages=['help me with the auth flow'],
        )
        sessions = list_sessions(sessions_root=self.root)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].session_id, 'sess-1')
        self.assertEqual(sessions[0].cwd, '/Users/dev/repos/myproj')
        self.assertEqual(sessions[0].turn_count, 1)
        self.assertEqual(
            sessions[0].first_user_message, 'help me with the auth flow',
        )

    def test_first_and_last_user_messages_are_distinct(self) -> None:
        _write_transcript(
            self.root, '-proj', 'sess-1',
            user_messages=['first thought', 'middle', 'last thought'],
        )
        session = list_sessions(sessions_root=self.root)[0]
        self.assertEqual(session.first_user_message, 'first thought')
        self.assertEqual(session.last_user_message, 'last thought')
        self.assertEqual(session.turn_count, 3)

    def test_orders_by_recency_descending(self) -> None:
        now = time.time()
        _write_transcript(
            self.root, '-proj', 'old', user_messages=['old'],
            file_mtime=now - 3600,
        )
        _write_transcript(
            self.root, '-proj', 'new', user_messages=['new'],
            file_mtime=now,
        )
        ids = [s.session_id for s in list_sessions(sessions_root=self.root)]
        self.assertEqual(ids, ['new', 'old'])

    def test_query_matches_cwd_substring(self) -> None:
        _write_transcript(
            self.root, '-Users-dev-repos-billing', 'sess-billing',
            cwd='/Users/dev/repos/billing',
            user_messages=['fix the invoice bug'],
        )
        _write_transcript(
            self.root, '-Users-dev-repos-marketing', 'sess-marketing',
            cwd='/Users/dev/repos/marketing',
            user_messages=['update the pricing page'],
        )
        results = list_sessions(sessions_root=self.root, query='billing')
        self.assertEqual([s.session_id for s in results], ['sess-billing'])

    def test_query_matches_user_message_substring(self) -> None:
        _write_transcript(
            self.root, '-proj-a', 'sess-a',
            user_messages=['fix the auth flow'],
        )
        _write_transcript(
            self.root, '-proj-b', 'sess-b',
            user_messages=['add a new dashboard'],
        )
        results = list_sessions(sessions_root=self.root, query='auth')
        self.assertEqual([s.session_id for s in results], ['sess-a'])

    def test_query_is_case_insensitive(self) -> None:
        _write_transcript(
            self.root, '-proj', 'sess',
            user_messages=['Fix The AUTH Flow'],
        )
        results = list_sessions(sessions_root=self.root, query='auth')
        self.assertEqual(len(results), 1)

    def test_max_results_is_respected(self) -> None:
        for n in range(5):
            _write_transcript(
                self.root, f'-proj-{n}', f'sess-{n}',
                user_messages=[f'task {n}'],
            )
        results = list_sessions(sessions_root=self.root, max_results=2)
        self.assertEqual(len(results), 2)

    def test_malformed_jsonl_lines_are_skipped(self) -> None:
        project_dir = self.root / '-proj'
        project_dir.mkdir()
        path = project_dir / 'sess.jsonl'
        path.write_text(
            'this is not json\n'
            + json.dumps({
                'type': 'user',
                'sessionId': 'sess',
                'cwd': '/proj',
                'message': {'content': 'good message'},
            })
            + '\nstill not json\n',
            encoding='utf-8',
        )
        results = list_sessions(sessions_root=self.root)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].first_user_message, 'good message')

    def test_user_message_with_text_part_list_is_extracted(self) -> None:
        _write_transcript(
            self.root, '-proj', 'sess',
            extra_lines=[{
                'type': 'user',
                'sessionId': 'sess',
                'cwd': '/proj',
                'message': {
                    'content': [
                        {'type': 'tool_result', 'content': 'output'},
                        {'type': 'text', 'text': 'the actual question'},
                    ],
                },
            }],
        )
        session = list_sessions(sessions_root=self.root)[0]
        self.assertEqual(session.first_user_message, 'the actual question')

    def test_tool_result_user_records_do_not_provide_preview(self) -> None:
        # A user record carrying only a tool_result (no text) should
        # increment turn count but not overwrite a meaningful preview.
        _write_transcript(
            self.root, '-proj', 'sess',
            user_messages=['real question'],
            extra_lines=[{
                'type': 'user',
                'sessionId': 'sess',
                'cwd': '/proj',
                'message': {
                    'content': [
                        {'type': 'tool_result', 'content': 'tool output'},
                    ],
                },
            }],
        )
        session = list_sessions(sessions_root=self.root)[0]
        self.assertEqual(session.first_user_message, 'real question')
        self.assertEqual(session.last_user_message, 'real question')
        self.assertEqual(session.turn_count, 2)

    def test_long_preview_is_clipped(self) -> None:
        long_text = 'x' * 500
        _write_transcript(
            self.root, '-proj', 'sess',
            user_messages=[long_text],
        )
        session = list_sessions(sessions_root=self.root)[0]
        self.assertLess(len(session.first_user_message), 500)
        self.assertTrue(session.first_user_message.endswith('…'))

    def test_default_sessions_root_uses_env_override(self) -> None:
        with patch.dict(
            os.environ,
            {CLAUDE_SESSIONS_ROOT_ENV_KEY: str(self.root)},
            clear=False,
        ):
            self.assertEqual(default_sessions_root(), self.root)

    def test_metadata_to_dict_is_json_serialisable(self) -> None:
        meta = ClaudeSessionMetadata(
            session_id='sess',
            cwd='/proj',
            last_modified_epoch=1.5,
            turn_count=2,
            first_user_message='hi',
            last_user_message='bye',
            transcript_path='/tmp/sess.jsonl',
        )
        self.assertEqual(json.loads(json.dumps(meta.to_dict())), meta.to_dict())


class SessionManagerAdoptionTests(unittest.TestCase):
    """``ClaudeSessionManager.adopt_session_id`` writes the id back."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_dir = Path(self._tmp.name)
        self.manager = ClaudeSessionManager(state_dir=self.state_dir)

    def test_adopt_creates_record_when_none_exists(self) -> None:
        record = self.manager.adopt_session_id(
            'PROJ-1', claude_session_id='abc-def',
        )
        self.assertEqual(record.task_id, 'PROJ-1')
        self.assertEqual(record.claude_session_id, 'abc-def')
        self.assertEqual(record.status, SESSION_STATUS_TERMINATED)

    def test_adopt_overwrites_existing_session_id(self) -> None:
        self.manager.adopt_session_id('PROJ-1', claude_session_id='first')
        self.manager.adopt_session_id('PROJ-1', claude_session_id='second')
        self.assertEqual(
            self.manager.get_record('PROJ-1').claude_session_id,
            'second',
        )

    def test_adopt_persists_record_to_disk(self) -> None:
        self.manager.adopt_session_id(
            'PROJ-1', claude_session_id='abc-def',
            task_summary='fix the bug',
        )
        # New manager instance reads the persisted record from disk
        # at construction.
        fresh = ClaudeSessionManager(state_dir=self.state_dir)
        record = fresh.get_record('PROJ-1')
        self.assertIsNotNone(record)
        self.assertEqual(record.claude_session_id, 'abc-def')
        self.assertEqual(record.task_summary, 'fix the bug')

    def test_adopt_refuses_empty_session_id(self) -> None:
        with self.assertRaisesRegex(ValueError, 'must be non-empty'):
            self.manager.adopt_session_id('PROJ-1', claude_session_id='')

    def test_adopt_strips_whitespace_around_session_id(self) -> None:
        record = self.manager.adopt_session_id(
            'PROJ-1', claude_session_id='  abc-def\n',
        )
        self.assertEqual(record.claude_session_id, 'abc-def')

    def test_adopt_does_not_change_cwd_so_kato_keeps_workspace_isolation(self) -> None:
        # Adoption MUST NOT repoint kato's spawn cwd at the source
        # session's directory. The operator wants kato to run
        # against its per-task workspace clone (an isolated copy)
        # so it can review changes against a clean worktree, not
        # against their live editor checkout. A short-lived
        # experiment with the opposite behaviour broke that
        # invariant — kato edited the dev's checkout in-place and
        # mixed git state. This test locks the safe default down.
        # Pre-set a cwd as if a previous spawn populated it.
        first = self.manager.adopt_session_id('PROJ-1', claude_session_id='abc-def')
        first.cwd = '/wks/PROJ-1/admin-backend'
        # Re-adopt — record.cwd must be untouched by the adoption.
        self.manager.adopt_session_id('PROJ-1', claude_session_id='ghi-jkl')
        self.assertEqual(
            self.manager.get_record('PROJ-1').cwd,
            '/wks/PROJ-1/admin-backend',
        )

    def test_adopt_does_not_overwrite_existing_task_summary(self) -> None:
        self.manager.adopt_session_id(
            'PROJ-1', claude_session_id='first',
            task_summary='first summary',
        )
        self.manager.adopt_session_id(
            'PROJ-1', claude_session_id='second',
            task_summary='second summary',
        )
        record = self.manager.get_record('PROJ-1')
        # First summary stays — the operator is adopting an existing
        # conversation, not redefining the task.
        self.assertEqual(record.task_summary, 'first summary')


class ProjectDirEncodingTests(unittest.TestCase):
    """``claude_project_dir_for_cwd`` matches Claude Code's on-disk layout."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._env_patch = patch.dict(
            os.environ,
            {CLAUDE_SESSIONS_ROOT_ENV_KEY: self._tmp.name},
            clear=False,
        )
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)

    def test_encodes_unix_cwd_with_dash_separator(self) -> None:
        # Replace ``/`` with ``-``; leading slash becomes leading dash.
        result = claude_project_dir_for_cwd('/Users/shay/repos/myproj')
        self.assertEqual(
            result,
            Path(self._tmp.name) / '-Users-shay-repos-myproj',
        )

    def test_collapses_to_workspace_root_under_env_override(self) -> None:
        # Override pins the projects root to a temp dir so tests
        # don't write into the operator's real ~/.claude.
        result = claude_project_dir_for_cwd('/x/y')
        self.assertTrue(str(result).startswith(self._tmp.name))

    def test_encodes_windows_cwd_collapsing_drive_colon_and_backslashes(
        self,
    ) -> None:
        # On Windows Claude Code flattens both the drive colon AND each
        # backslash to ``-`` — ``C:\Codes\proj`` becomes
        # ``C--Codes-proj`` (the consecutive ``:\`` produces two dashes
        # in a row). Replacing only ``os.sep`` left the colon intact so
        # the migrated JSONL was unreachable from --resume.
        with patch('os.path.abspath', side_effect=lambda p: p):
            result = claude_project_dir_for_cwd(r'C:\Codes\UNA-2489-proj')
        self.assertEqual(
            result.name,
            'C--Codes-UNA-2489-proj',
        )


class MigrateSessionToWorkspaceTests(unittest.TestCase):
    """``migrate_session_to_workspace`` copies the JSONL to the target cwd's project dir."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        # Override Claude Code's project root so tests don't pollute
        # the host's ~/.claude/projects.
        self._env_patch = patch.dict(
            os.environ,
            {CLAUDE_SESSIONS_ROOT_ENV_KEY: str(self.root)},
            clear=False,
        )
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)
        # Source: a fake "VS Code" session JSONL stored under a
        # different cwd's encoded project directory.
        self.source_project_dir = self.root / '-Users-dev-repos-myproj'
        self.source_project_dir.mkdir()
        self.source_path = self.source_project_dir / 'sess-abc.jsonl'
        self.source_path.write_text(
            json.dumps({'type': 'user', 'sessionId': 'sess-abc',
                        'cwd': '/Users/dev/repos/myproj'}) + '\n',
            encoding='utf-8',
        )

    def test_copies_jsonl_into_target_cwd_project_dir(self) -> None:
        target_cwd = '/Users/dev/.kato/workspaces/PROJ-1/myproj'
        result = migrate_session_to_workspace(
            transcript_path=str(self.source_path),
            target_cwd=target_cwd,
        )
        self.assertIsNotNone(result)
        # File now also exists at the kato cwd's project dir.
        # Claude Code's encoding only replaces ``/`` with ``-``;
        # dots in path segments (``.kato``) are preserved verbatim.
        kato_dir = self.root / '-Users-dev-.kato-workspaces-PROJ-1-myproj'
        self.assertTrue((kato_dir / 'sess-abc.jsonl').is_file())

    def test_returns_none_when_source_missing(self) -> None:
        result = migrate_session_to_workspace(
            transcript_path=str(self.root / 'nope.jsonl'),
            target_cwd='/x/y',
        )
        self.assertIsNone(result)

    def test_returns_none_when_target_cwd_empty(self) -> None:
        result = migrate_session_to_workspace(
            transcript_path=str(self.source_path),
            target_cwd='',
        )
        self.assertIsNone(result)

    def test_idempotent_when_destination_already_exists(self) -> None:
        target_cwd = '/Users/dev/.kato/workspaces/PROJ-1/myproj'
        # First call copies.
        first = migrate_session_to_workspace(
            transcript_path=str(self.source_path),
            target_cwd=target_cwd,
        )
        # Second call doesn't error and returns the same destination.
        second = migrate_session_to_workspace(
            transcript_path=str(self.source_path),
            target_cwd=target_cwd,
        )
        self.assertEqual(first, second)
        self.assertTrue(first.is_file())

    def test_creates_target_dir_when_missing(self) -> None:
        # Cwd has never been used by Claude Code, so its project
        # dir doesn't exist yet. Migration creates it.
        target_cwd = '/totally/new/path/never/used'
        result = migrate_session_to_workspace(
            transcript_path=str(self.source_path),
            target_cwd=target_cwd,
        )
        self.assertIsNotNone(result)
        self.assertTrue(result.is_file())

    def test_preserves_jsonl_content(self) -> None:
        result = migrate_session_to_workspace(
            transcript_path=str(self.source_path),
            target_cwd='/Users/dev/.kato/workspaces/PROJ-1/myproj',
        )
        self.assertEqual(
            result.read_text(encoding='utf-8'),
            self.source_path.read_text(encoding='utf-8'),
        )


if __name__ == '__main__':
    unittest.main()
