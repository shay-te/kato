"""Guards for the silent-resume-ignore failure mode.

Field bug (Windows): ``claude --resume <id>`` sometimes starts a FRESH
session under a NEW id instead of resuming — no error, no "No
conversation found" on stderr — when another live CLI process still
holds the transcript (kato's old wrapper-only kill orphaned the real
CLI behind the npm ``claude.cmd`` shim). The user then chats with a
blank conversation that LOOKS resumed: the "Claude forgot what he was
doing" bug.

Three layers now defend against it, each tested here:

1. ``StreamingClaudeSession`` raises the ``resume_was_ignored`` flag
   when the init event announces an id different from the requested
   resume id.
2. ``ClaudeSessionManager.start_session`` refuses such a spawn —
   terminates the impostor and raises, keeping the pinned id intact.
3. ``get_session`` discards a live session whose flag flipped AFTER
   the spawn-time verdict window.

Plus the pre-spawn guard: ``start_session`` asks the registry to
release any leftover holder of the resume id before spawning.
"""

from __future__ import annotations

import tempfile
import time
import unittest
from unittest import mock

from claude_core_lib.claude_core_lib.session.manager import ClaudeSessionManager
from claude_core_lib.claude_core_lib.session.streaming import (
    SessionEvent,
    StreamingClaudeSession,
)


class _StubSession:
    """Minimal stand-in mirroring the StreamingClaudeSession surface."""

    def __init__(self, **kwargs):
        self._kwargs = kwargs
        self._task_id = kwargs.get('task_id', '')
        self._cwd = kwargs.get('cwd', '')
        self._agent_session_id = (
            kwargs.get('resume_session_id', '') or 'fresh-id'
        )
        self._alive = True
        self.terminated = False
        # Behaviour knobs the tests flip per scenario.
        self.resume_confirmed = False
        self.resume_was_ignored = False

    @property
    def task_id(self):
        return self._task_id

    @property
    def cwd(self):
        return self._cwd

    @property
    def agent_session_id(self):
        return self._agent_session_id

    @property
    def is_alive(self):
        return self._alive

    def start(self, *, initial_prompt=''):
        pass

    def stderr_snapshot(self):
        return []

    @property
    def terminal_event(self):
        return None

    def terminate(self):
        self.terminated = True
        self._alive = False


def _factory(instances, **behaviour):
    """Session factory recording every spawn and applying ``behaviour``."""

    def build(**kwargs):
        session = _StubSession(**kwargs)
        for key, value in behaviour.items():
            setattr(session, key, value)
        instances.append(session)
        return session

    return build


def _manager(state_dir, factory):
    manager = ClaudeSessionManager(state_dir=state_dir, session_factory=factory)
    # The pre-spawn registry guard scans the real ~/.claude/sessions by
    # default; tests stay hermetic by stubbing it out.
    manager._terminate_stale_resume_holders = mock.Mock()
    return manager


def _persist_resume_id(manager, task_id, instances):
    """First spawn + terminate so the next spawn resumes 'fresh-id'."""
    manager.start_session(task_id=task_id, cwd='/tmp/w')
    manager.terminate_session(task_id)
    instances.clear()


class ResumeIgnoredSpawnGuardTests(unittest.TestCase):
    def test_ignored_resume_is_terminated_and_raises(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            instances: list[_StubSession] = []
            manager = _manager(td, _factory(instances))
            _persist_resume_id(manager, 'T1', instances)
            manager._session_factory = _factory(
                instances, resume_was_ignored=True,
            )
            with self.assertRaises(RuntimeError) as ctx:
                manager.start_session(task_id='T1', cwd='/tmp/w')
            self.assertIn('ignored resume id fresh-id', str(ctx.exception))
            self.assertTrue(instances[0].terminated)
            # The pinned id survives the refusal — the whole point.
            self.assertEqual(
                manager.get_record('T1').agent_session_id, 'fresh-id',
            )

    def test_confirmed_resume_returns_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            instances: list[_StubSession] = []
            manager = _manager(td, _factory(instances))
            _persist_resume_id(manager, 'T1', instances)
            manager._session_factory = _factory(
                instances, resume_confirmed=True,
            )
            started = time.monotonic()
            session = manager.start_session(task_id='T1', cwd='/tmp/w')
            elapsed = time.monotonic() - started
            self.assertIs(session, instances[0])
            self.assertFalse(instances[0].terminated)
            # The verdict wait must exit on the confirmation, not sit
            # out its full polling window.
            self.assertLess(elapsed, 2.0)

    def test_spawn_guard_asks_registry_to_release_resume_holders(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            instances: list[_StubSession] = []
            manager = _manager(td, _factory(instances))
            _persist_resume_id(manager, 'T1', instances)
            manager._session_factory = _factory(
                instances, resume_confirmed=True,
            )
            manager.start_session(task_id='T1', cwd='/tmp/w')
            manager._terminate_stale_resume_holders.assert_called_with(
                'T1', 'fresh-id',
            )

    def test_release_helper_calls_registry_module(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            manager = ClaudeSessionManager(
                state_dir=td, session_factory=_factory([]),
            )
            with mock.patch(
                'claude_core_lib.claude_core_lib.session.registry.'
                'release_session_holders',
                return_value=True,
            ) as release:
                manager._terminate_stale_resume_holders('T1', 'sid-9')
            release.assert_called_once_with('sid-9', logger=manager.logger)

    def test_release_helper_skips_blank_resume_id(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            manager = ClaudeSessionManager(
                state_dir=td, session_factory=_factory([]),
            )
            with mock.patch(
                'claude_core_lib.claude_core_lib.session.registry.'
                'release_session_holders',
            ) as release:
                manager._terminate_stale_resume_holders('T1', '')
            release.assert_not_called()

    def test_release_helper_survives_registry_errors(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            manager = ClaudeSessionManager(
                state_dir=td, session_factory=_factory([]),
            )
            with mock.patch(
                'claude_core_lib.claude_core_lib.session.registry.'
                'release_session_holders',
                side_effect=RuntimeError('registry exploded'),
            ):
                manager._terminate_stale_resume_holders('T1', 'sid-9')

    def test_release_helper_logs_when_session_stays_held(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            manager = ClaudeSessionManager(
                state_dir=td, session_factory=_factory([]),
            )
            with mock.patch(
                'claude_core_lib.claude_core_lib.session.registry.'
                'release_session_holders',
                return_value=False,
            ), mock.patch.object(manager, 'logger') as logger:
                manager._terminate_stale_resume_holders('T1', 'sid-9')
            logger.warning.assert_called_once()


class ResumeIgnoredLiveDiscardTests(unittest.TestCase):
    def test_get_session_discards_late_ignored_resume(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            instances: list[_StubSession] = []
            manager = _manager(td, _factory(instances))
            _persist_resume_id(manager, 'T1', instances)
            manager._session_factory = _factory(
                instances, resume_confirmed=True,
            )
            session = manager.start_session(task_id='T1', cwd='/tmp/w')
            # The init verdict arrives late: the live process turns out
            # to be a fresh blank conversation, not the resumed one.
            session.resume_was_ignored = True
            self.assertIsNone(manager.get_session('T1'))
            self.assertTrue(session.terminated)
            # The pinned id is untouched; the next spawn resumes it.
            self.assertEqual(
                manager.get_record('T1').agent_session_id, 'fresh-id',
            )


class StreamingResumeFlagTests(unittest.TestCase):
    @staticmethod
    def _init_event(session_id: str) -> SessionEvent:
        return SessionEvent(raw={
            'type': 'system',
            'subtype': 'init',
            'session_id': session_id,
        })

    def test_init_echoing_resume_id_confirms(self) -> None:
        session = StreamingClaudeSession(
            task_id='T1', resume_session_id='resume-id-1',
        )
        # ``_build_command`` pins ``agent_session_id`` synchronously —
        # the same point the real spawn path does it.
        session._build_command()
        session._maybe_capture_session_id(self._init_event('resume-id-1'))
        self.assertTrue(session.resume_confirmed)
        self.assertFalse(session.resume_was_ignored)

    def test_init_with_different_id_flags_ignored_resume(self) -> None:
        session = StreamingClaudeSession(
            task_id='T1', resume_session_id='resume-id-1',
        )
        session._build_command()
        session._maybe_capture_session_id(self._init_event('other-id-2'))
        self.assertTrue(session.resume_was_ignored)
        self.assertFalse(session.resume_confirmed)
        # The pinned id is kept — kato never adopts the impostor's id.
        self.assertEqual(session.agent_session_id, 'resume-id-1')

    def test_fresh_spawn_mismatch_does_not_flag_ignored(self) -> None:
        session = StreamingClaudeSession(task_id='T1')
        session._build_command()
        session._maybe_capture_session_id(self._init_event('actual-id-9'))
        self.assertFalse(session.resume_was_ignored)
        self.assertFalse(session.resume_confirmed)

    def test_wait_for_stale_resume_failure_exits_early_on_flags(self) -> None:
        for flag in ('resume_confirmed', 'resume_was_ignored'):
            session = _StubSession(resume_session_id='resume-id-1')
            setattr(session, flag, True)
            started = time.monotonic()
            result = ClaudeSessionManager._wait_for_stale_resume_failure(
                session, 'resume-id-1', max_wait_seconds=5.0,
            )
            elapsed = time.monotonic() - started
            self.assertFalse(result)
            self.assertLess(elapsed, 1.0, f'no early exit for {flag}')


class WindowsShimBypassTests(unittest.TestCase):
    """The spawn must bypass the npm ``claude.cmd`` batch shim.

    cmd.exe silently cuts its command line at the first raw newline
    (and caps it at ~8K chars). Kato's ``--append-system-prompt``
    value is multiline, so spawning through the shim dropped every
    later argument — ``--resume``, ``--session-id``, ``--add-dir`` —
    and the CLI started a fresh, memoryless session on every respawn:
    the root cause of the Windows resume-amnesia bug.
    """

    def _shim(self, tmp: str, body: str) -> str:
        import os as _os
        shim_path = _os.path.join(tmp, 'claude.cmd')
        with open(shim_path, 'w', encoding='utf-8') as fh:
            fh.write(body)
        return shim_path

    def test_resolves_native_exe_shim(self) -> None:
        # Current npm package shape: the shim forwards to a bundled
        # native binary (this is the shape on real Windows installs).
        import os as _os
        import tempfile as _tempfile
        from claude_core_lib.claude_core_lib.cli_client import ClaudeCliClient
        with _tempfile.TemporaryDirectory() as tmp:
            exe_dir = _os.path.join(
                tmp, 'node_modules', '@anthropic-ai', 'claude-code', 'bin',
            )
            _os.makedirs(exe_dir)
            exe_path = _os.path.join(exe_dir, 'claude.exe')
            with open(exe_path, 'wb') as fh:
                fh.write(b'MZ')
            shim = self._shim(
                tmp,
                '@ECHO off\r\nSETLOCAL\r\nSET dp0=%~dp0\r\n'
                '"%dp0%\\node_modules\\@anthropic-ai\\claude-code\\bin\\claude.exe"   %*\r\n',
            )
            with mock.patch(
                'claude_core_lib.claude_core_lib.cli_client.os',
            ) as os_mod:
                os_mod.name = 'nt'
                result = ClaudeCliClient._resolve_windows_node_invocation(shim)
        self.assertIsNotNone(result)
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0].lower().endswith('claude.exe'))

    def test_tilde_dp0_prefix_also_resolves(self) -> None:
        import os as _os
        import tempfile as _tempfile
        from claude_core_lib.claude_core_lib.cli_client import ClaudeCliClient
        with _tempfile.TemporaryDirectory() as tmp:
            exe_path = _os.path.join(tmp, 'real.exe')
            with open(exe_path, 'wb') as fh:
                fh.write(b'MZ')
            shim = self._shim(tmp, '"%~dp0\\real.exe" %*\r\n')
            with mock.patch(
                'claude_core_lib.claude_core_lib.cli_client.os',
            ) as os_mod:
                os_mod.name = 'nt'
                result = ClaudeCliClient._resolve_windows_node_invocation(shim)
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0].lower().endswith('real.exe'))

    def test_missing_exe_target_returns_none(self) -> None:
        import tempfile as _tempfile
        from claude_core_lib.claude_core_lib.cli_client import ClaudeCliClient
        with _tempfile.TemporaryDirectory() as tmp:
            shim = self._shim(tmp, '"%dp0%\\gone\\claude.exe" %*\r\n')
            with mock.patch(
                'claude_core_lib.claude_core_lib.cli_client.os',
            ) as os_mod:
                os_mod.name = 'nt'
                result = ClaudeCliClient._resolve_windows_node_invocation(shim)
        self.assertIsNone(result)

    def test_streaming_build_command_bypasses_shim(self) -> None:
        from claude_core_lib.claude_core_lib.cli_client import ClaudeCliClient
        session = StreamingClaudeSession(task_id='T1', binary='claude')
        with mock.patch.object(
            ClaudeCliClient, '_resolve_windows_node_invocation',
            return_value=['C:\\real\\claude.exe'],
        ):
            command = session._build_command()
        self.assertEqual(command[0], 'C:\\real\\claude.exe')

    def test_streaming_build_command_falls_back_to_which_result(self) -> None:
        from claude_core_lib.claude_core_lib.cli_client import ClaudeCliClient
        session = StreamingClaudeSession(task_id='T1', binary='claude')
        with mock.patch.object(
            ClaudeCliClient, '_resolve_windows_node_invocation',
            return_value=None,
        ), mock.patch(
            'claude_core_lib.claude_core_lib.session.streaming.shutil.which',
            return_value='/usr/local/bin/claude',
        ):
            command = session._build_command()
        self.assertEqual(command[0], '/usr/local/bin/claude')


class ArgvOrderTests(unittest.TestCase):
    """Session identity must precede the multiline system prompt.

    Defense-in-depth for any spawn that still degrades to a batch
    shim: truncation at the system prompt's first newline may cost
    prompt text, never the ``--resume``/``--session-id`` pin or the
    ``--add-dir`` sandbox scope.
    """

    def _command(self, **kwargs) -> list[str]:
        session = StreamingClaudeSession(
            task_id='T1',
            additional_dirs=['/extra/repo'],
            **kwargs,
        )
        with mock.patch(
            'claude_core_lib.claude_core_lib.session.streaming.'
            'build_appended_system_prompt',
            return_value='line1\nline2',
        ):
            return session._build_command()

    def test_resume_and_add_dir_precede_system_prompt(self) -> None:
        command = self._command(resume_session_id='resume-id-1')
        self.assertLess(
            command.index('--resume'),
            command.index('--append-system-prompt'),
        )
        self.assertLess(
            command.index('--add-dir'),
            command.index('--append-system-prompt'),
        )

    def test_session_id_precedes_system_prompt_on_fresh_spawn(self) -> None:
        command = self._command()
        self.assertLess(
            command.index('--session-id'),
            command.index('--append-system-prompt'),
        )

    def test_one_shot_resume_precedes_system_prompt(self) -> None:
        from claude_core_lib.claude_core_lib.cli_client import ClaudeCliClient
        client = ClaudeCliClient(binary='claude')
        with mock.patch(
            'claude_core_lib.claude_core_lib.cli_client.'
            'build_appended_system_prompt',
            return_value='line1\nline2',
        ):
            command = client._build_command(
                additional_dirs=['/extra/repo'],
                agent_session_id='resume-id-1',
                resolve_binary=False,
            )
        self.assertLess(
            command.index('--resume'),
            command.index('--append-system-prompt'),
        )
        self.assertLess(
            command.index('--add-dir'),
            command.index('--append-system-prompt'),
        )


class WindowsTreeKillTests(unittest.TestCase):
    """The terminate path must kill the WHOLE tree on Windows.

    ``send_signal(SIGTERM)``/``kill()`` are ``TerminateProcess`` on the
    direct child only; with the npm ``claude.cmd`` shim that child is a
    cmd.exe wrapper and the real CLI survives — the orphan that caused
    the resume-amnesia bug in the first place.
    """

    @staticmethod
    def _session_with_proc(proc):
        session = StreamingClaudeSession(task_id='T1')
        session._proc = proc
        return session

    def test_sigterm_escalation_uses_tree_kill_on_windows(self) -> None:
        from claude_core_lib.claude_core_lib.session import streaming
        proc = mock.Mock()
        proc.pid = 4242
        proc.wait.return_value = 0  # exits right after the tree kill
        session = self._session_with_proc(proc)
        with mock.patch.object(streaming, '_IS_WINDOWS', True), \
                mock.patch.object(streaming, 'kill_process_tree') as kill:
            session._escalate_to_sigterm(proc)
        kill.assert_called_once_with(4242, logger=session.logger)
        proc.send_signal.assert_not_called()

    def test_sigterm_escalation_uses_signal_on_posix(self) -> None:
        from claude_core_lib.claude_core_lib.session import streaming
        proc = mock.Mock()
        proc.pid = 4242
        proc.wait.return_value = 0
        session = self._session_with_proc(proc)
        with mock.patch.object(streaming, '_IS_WINDOWS', False), \
                mock.patch.object(streaming, 'kill_process_tree') as kill:
            session._escalate_to_sigterm(proc)
        kill.assert_not_called()
        proc.send_signal.assert_called_once()

    def test_failed_tree_kill_falls_back_to_signal(self) -> None:
        # taskkill unavailable / exploding must degrade to the old
        # wrapper-only kill, never crash the teardown path.
        from claude_core_lib.claude_core_lib.session import streaming
        proc = mock.Mock()
        proc.pid = 4242
        proc.wait.return_value = 0
        session = self._session_with_proc(proc)
        with mock.patch.object(streaming, '_IS_WINDOWS', True), \
                mock.patch.object(
                    streaming, 'kill_process_tree',
                    side_effect=OSError('no taskkill'),
                ):
            session._escalate_to_sigterm(proc)
        proc.send_signal.assert_called_once()

    def test_kill_escalation_also_tree_kills_on_windows(self) -> None:
        from claude_core_lib.claude_core_lib.session import streaming
        proc = mock.Mock()
        proc.pid = 4242
        proc.wait.return_value = 0
        session = self._session_with_proc(proc)
        with mock.patch.object(streaming, '_IS_WINDOWS', True), \
                mock.patch.object(streaming, 'kill_process_tree') as kill:
            session._escalate_to_kill(proc)
        kill.assert_called_once_with(4242, logger=session.logger)
        proc.kill.assert_called_once()


if __name__ == '__main__':
    unittest.main()
