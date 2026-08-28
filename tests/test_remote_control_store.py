"""Tests for the persistent per-task Remote Control preference store.

The Remote Control bridge dies with the Claude subprocess, and kato respawns
those constantly, so the operator's choice is persisted here and re-applied on
every spawn. The path is env-overridable so the test never touches the real
``~/.kato``.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import time
from types import SimpleNamespace

from kato_core_lib.helpers.remote_control_store import (
    apply_remote_control,
    is_remote_control_enabled,
    read_remote_control_tasks,
    remote_control_session_name,
    schedule_remote_control_for_spawn,
    set_remote_control_enabled,
)


class _Session:
    def __init__(self, alive=True, error='') -> None:
        self.is_alive = alive
        self._error = error
        self.calls: list[tuple[bool, str]] = []

    def set_remote_control(self, enabled, name='', timeout=None):  # noqa: ARG002
        self.calls.append((bool(enabled), name))
        if self._error:
            raise RuntimeError(self._error)
        return {'enabled': bool(enabled), 'session_url': 'https://x/y'}


def _wait_for(predicate, timeout=2.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class RemoteControlStoreTests(unittest.TestCase):

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self._path = str(Path(self._td.name) / 'remote_control.json')
        patcher = unittest.mock.patch.dict(
            os.environ, {'KATO_REMOTE_CONTROL_PATH': self._path},
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_empty_when_no_file(self) -> None:
        self.assertEqual(read_remote_control_tasks(), set())
        self.assertFalse(is_remote_control_enabled('PROJ-1'))

    def test_set_then_read_roundtrip(self) -> None:
        set_remote_control_enabled('PROJ-1', True)
        self.assertEqual(read_remote_control_tasks(), {'PROJ-1'})
        self.assertTrue(is_remote_control_enabled('PROJ-1'))
        # Survives a fresh read (i.e. a "restart"): the file is on disk.
        self.assertTrue(Path(self._path).is_file())

    def test_clearing_removes_only_that_task(self) -> None:
        set_remote_control_enabled('PROJ-1', True)
        set_remote_control_enabled('PROJ-2', True)
        set_remote_control_enabled('PROJ-1', False)
        self.assertEqual(read_remote_control_tasks(), {'PROJ-2'})

    def test_task_id_stored_verbatim(self) -> None:
        # Stored exactly as sent (the canonical platform id) so it reloads
        # straight into the route-keyed override map, which is not
        # case-normalized.
        set_remote_control_enabled('Proj-Mixed-9', True)
        self.assertEqual(
            json.loads(Path(self._path).read_text(encoding='utf-8')),
            ['Proj-Mixed-9'],
        )

    def test_blank_task_id_is_ignored(self) -> None:
        set_remote_control_enabled('   ', True)
        set_remote_control_enabled('', True)
        self.assertEqual(read_remote_control_tasks(), set())

    def test_repeat_write_is_a_no_op(self) -> None:
        set_remote_control_enabled('PROJ-1', True)
        mtime = Path(self._path).stat().st_mtime_ns
        set_remote_control_enabled('PROJ-1', True)
        self.assertEqual(Path(self._path).stat().st_mtime_ns, mtime)

    def test_clearing_an_unknown_task_does_not_create_the_file(self) -> None:
        set_remote_control_enabled('PROJ-404', False)
        self.assertFalse(Path(self._path).exists())

    def test_unreadable_file_reads_as_empty(self) -> None:
        # A corrupt file must not brick the composer's toggle — it reads as
        # "nobody has this on", and the next write repairs it.
        Path(self._path).write_text('{not json', encoding='utf-8')
        self.assertEqual(read_remote_control_tasks(), set())
        set_remote_control_enabled('PROJ-1', True)
        self.assertEqual(read_remote_control_tasks(), {'PROJ-1'})

    def test_wrong_shape_reads_as_empty(self) -> None:
        Path(self._path).write_text('{"PROJ-1": true}', encoding='utf-8')
        self.assertEqual(read_remote_control_tasks(), set())


class ApplyRemoteControlTests(unittest.TestCase):
    """Toggling one live session, and what counts as "nothing to do"."""

    def test_names_the_session_after_the_task(self) -> None:
        session = _Session()
        apply_remote_control(session, 'PROJ-1', True)
        self.assertEqual(session.calls, [(True, 'kato PROJ-1')])
        self.assertEqual(remote_control_session_name('PROJ-1'), 'kato PROJ-1')

    def test_no_session_is_not_an_error(self) -> None:
        # The preference is stored either way and the next spawn applies it.
        self.assertEqual(apply_remote_control(None, 'PROJ-1', True), {})

    def test_a_dead_session_is_left_alone(self) -> None:
        session = _Session(alive=False)
        self.assertEqual(apply_remote_control(session, 'PROJ-1', True), {})
        self.assertEqual(session.calls, [])

    def test_a_backend_without_the_feature_is_left_alone(self) -> None:
        # Codex sessions have no ``set_remote_control`` at all.
        self.assertEqual(
            apply_remote_control(SimpleNamespace(is_alive=True), 'PROJ-1', True), {},
        )

    def test_a_refusal_propagates(self) -> None:
        # The route needs the CLI's own words to show the operator.
        with self.assertRaisesRegex(RuntimeError, 'not signed in'):
            apply_remote_control(_Session(error='not signed in'), 'PROJ-1', True)


class ScheduleRemoteControlForSpawnTests(unittest.TestCase):
    """The spawn-time re-bridge.

    Lives on the runner's single spawn funnel, not on the webserver's
    chat-send route: a comment run and a review fix respawn through the same
    funnel, and hooking only the chat route meant the bridge survived the
    operator typing and nothing else — it vanished from their phone the
    moment kato did any work on its own.
    """

    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        patcher = unittest.mock.patch.dict(os.environ, {
            'KATO_REMOTE_CONTROL_PATH': str(Path(self._td.name) / 'rc.json'),
        })
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_re_enables_a_task_the_operator_switched_on(self) -> None:
        set_remote_control_enabled('PROJ-1', True)
        session = _Session()
        schedule_remote_control_for_spawn(session, 'PROJ-1')
        self.assertTrue(_wait_for(lambda: session.calls == [(True, 'kato PROJ-1')]))

    def test_leaves_a_task_that_is_switched_off_alone(self) -> None:
        session = _Session()
        schedule_remote_control_for_spawn(session, 'PROJ-1')
        time.sleep(0.1)
        self.assertEqual(session.calls, [])

    def test_a_failed_re_enable_never_reaches_the_spawn(self) -> None:
        # A bridge that cannot come back must not take the session with it.
        set_remote_control_enabled('PROJ-1', True)
        session = _Session(error='bridge refused')
        schedule_remote_control_for_spawn(session, 'PROJ-1')
        self.assertTrue(_wait_for(lambda: len(session.calls) == 1))

    def test_a_backend_without_the_feature_is_skipped(self) -> None:
        set_remote_control_enabled('PROJ-1', True)
        schedule_remote_control_for_spawn(SimpleNamespace(is_alive=True), 'PROJ-1')


if __name__ == '__main__':
    unittest.main()
