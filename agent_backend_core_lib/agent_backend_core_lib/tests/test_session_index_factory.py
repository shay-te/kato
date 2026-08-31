"""Tests for the per-backend session-index dispatch.

The factory exists so that adoption — handing the host a conversation the
operator already started in a CLI — is not hard-wired to one backend. Its
whole job is picking the right transport's index, so these tests are about
that pick: the correct module answers, an unsupported backend degrades to
empty rather than raising, and the two backends' differing transcript
storage is reported accurately.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent_backend_core_lib.agent_backend_core_lib.client.session_index_factory import (  # noqa: E501
    list_adoptable_sessions,
    requires_transcript_migration,
    supports_session_adoption,
)


class SupportsSessionAdoptionTests(unittest.TestCase):
    def test_the_cli_backends_keep_local_sessions(self) -> None:
        for name in ('claude', 'claude-code', 'codex', 'codex-cli'):
            with self.subTest(backend=name):
                self.assertTrue(supports_session_adoption(name))

    def test_a_server_side_backend_has_nothing_local_to_adopt(self) -> None:
        # OpenHands runs its sessions server-side; the caller uses this to
        # hide the control rather than offer one that can only come back empty.
        self.assertFalse(supports_session_adoption('openhands'))

    def test_an_unknown_backend_is_false_not_an_exception(self) -> None:
        self.assertFalse(supports_session_adoption('not-a-backend'))

    def test_an_empty_backend_is_false(self) -> None:
        # '' resolves to the historical OpenHands default, which has no store.
        self.assertFalse(supports_session_adoption(''))


class ListAdoptableSessionsTests(unittest.TestCase):
    def test_an_unsupported_backend_lists_nothing_rather_than_raising(self) -> None:
        # This feeds a picker. A bad selector should show "nothing to adopt",
        # never a 500.
        self.assertEqual(list_adoptable_sessions('nope'), [])
        self.assertEqual(list_adoptable_sessions('openhands'), [])

    def test_claude_is_read_through_claude_s_own_index(self) -> None:
        target = (
            'claude_core_lib.claude_core_lib.session.index.list_sessions'
        )
        with mock.patch(target, return_value=['row']) as listed:
            rows = list_adoptable_sessions('claude', query='auth')
        self.assertEqual(rows, ['row'])
        self.assertEqual(listed.call_args.kwargs['query'], 'auth')

    def test_codex_is_read_through_codex_s_own_index(self) -> None:
        # The point of the whole factory: a Codex request must NOT fall
        # through to Claude's store, which is what made adoption Claude-only.
        target = (
            'codex_core_lib.codex_core_lib.session.index.list_sessions'
        )
        with mock.patch(target, return_value=['codex-row']) as listed:
            rows = list_adoptable_sessions('codex', query='auth')
        self.assertEqual(rows, ['codex-row'])
        self.assertEqual(listed.call_args.kwargs['query'], 'auth')

    def test_max_results_reaches_the_backend(self) -> None:
        target = (
            'codex_core_lib.codex_core_lib.session.index.list_sessions'
        )
        with mock.patch(target, return_value=[]) as listed:
            list_adoptable_sessions('codex', max_results=7)
        self.assertEqual(listed.call_args.kwargs['max_results'], 7)

    def test_a_real_codex_rollout_is_listed_end_to_end(self) -> None:
        # Not mocked: proves the factory reaches an index that can actually
        # parse the CLI's on-disk format, rather than just any callable.
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            day = home / 'sessions' / '2026' / '08' / '30'
            day.mkdir(parents=True)
            rollout = day / 'rollout-2026-08-30T10-00-00-thread-abc123.jsonl'
            rollout.write_text(
                json.dumps({
                    'type': 'session_meta',
                    'payload': {'id': 'thread-abc123', 'cwd': '/work/proj'},
                }) + '\n'
                + json.dumps({
                    'type': 'response_item',
                    'payload': {
                        'type': 'message', 'role': 'user',
                        'content': [{'text': 'fix the auth bug'}],
                    },
                }) + '\n',
                encoding='utf-8',
            )
            with mock.patch.dict('os.environ', {'CODEX_HOME': str(home)}):
                rows = list_adoptable_sessions('codex')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].agent_session_id, 'thread-abc123')
        self.assertEqual(rows[0].cwd, '/work/proj')
        self.assertEqual(rows[0].first_user_message, 'fix the auth bug')


class RequiresTranscriptMigrationTests(unittest.TestCase):
    def test_claude_needs_a_snapshot(self) -> None:
        # Claude resolves a transcript through a cwd-keyed directory, so an
        # adopted one has to be placed under the workspace it will run in.
        self.assertTrue(requires_transcript_migration('claude'))

    def test_codex_does_not(self) -> None:
        # Codex resolves a rollout by id across one flat store: the file is
        # already where ``codex exec resume`` looks. Copying it would at best
        # be a no-op and at worst duplicate a transcript under a second id.
        self.assertFalse(requires_transcript_migration('codex'))

    def test_an_unknown_backend_does_not(self) -> None:
        self.assertFalse(requires_transcript_migration('not-a-backend'))



class RowShapeParityTests(unittest.TestCase):
    """Both backends must expose the fields a picker draws.

    The factory's contract is that a caller can render any row without
    knowing which backend answered. Nothing enforced that, so a field could
    be added to one index and quietly missing from the other — and the UI
    would render a blank cell for whichever backend lost the coin toss.
    """

    #: What a picker row actually reads.
    REQUIRED = {
        'agent_session_id', 'cwd', 'last_modified_epoch',
        'turn_count', 'first_user_message', 'last_user_message',
    }

    def _row_fields(self, module_path):
        import importlib
        module = importlib.import_module(module_path)
        for name in dir(module):
            value = getattr(module, name)
            if isinstance(value, type) and name.endswith('SessionMetadata'):
                return set(getattr(value, '__dataclass_fields__', {}))
        self.fail(f'no metadata dataclass in {module_path}')

    def test_claude_rows_carry_every_required_field(self) -> None:
        self.assertLessEqual(
            self.REQUIRED,
            self._row_fields('claude_core_lib.claude_core_lib.session.index'),
        )

    def test_codex_rows_carry_every_required_field(self) -> None:
        self.assertLessEqual(
            self.REQUIRED,
            self._row_fields('codex_core_lib.codex_core_lib.session.index'),
        )

if __name__ == '__main__':
    unittest.main()
