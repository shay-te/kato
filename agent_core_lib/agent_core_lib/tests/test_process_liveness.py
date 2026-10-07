"""Cross-platform process liveness and killing.

Every assertion here encodes a platform trap: probing with ``os.kill(pid, 0)``
would KILL the process on Windows, a single-process kill orphans the real CLI
behind an npm ``.cmd`` shim, and a recycled pid must never be killed on the
strength of a stale registry entry.

``IS_WINDOWS`` is patched so both branches run on one platform.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import unittest
from unittest import mock

from agent_core_lib.agent_core_lib.helpers import process_liveness


class KillProcessTreeTests(unittest.TestCase):
    def test_rejects_non_numeric_and_non_positive_pids(self) -> None:
        self.assertFalse(process_liveness.kill_process_tree('NaN'))
        self.assertFalse(process_liveness.kill_process_tree(0))
        self.assertFalse(process_liveness.kill_process_tree(-5))

    def test_init_is_never_signalled_nor_its_group(self) -> None:
        # ``int(MagicMock())`` is 1: a test that handed a mock process to the
        # real kill sent SIGKILL to pid 1 AND process group 1, which on a CI
        # runner took the runner itself down. Not even an attempt now.
        with mock.patch.object(process_liveness, 'IS_WINDOWS', False), \
                mock.patch.object(os, 'killpg') as killpg, \
                mock.patch.object(os, 'kill') as kill:
            self.assertFalse(process_liveness.kill_process_tree(1))
            self.assertFalse(process_liveness.kill_process_tree(mock.MagicMock()))
        killpg.assert_not_called()
        kill.assert_not_called()

    def test_windows_uses_taskkill_tree_force(self) -> None:
        completed = mock.Mock(returncode=0)
        with mock.patch.object(process_liveness, 'IS_WINDOWS', True), \
                mock.patch.object(
                    process_liveness.subprocess, 'run', return_value=completed,
                ) as run:
            self.assertTrue(process_liveness.kill_process_tree(4242))
        argv = run.call_args[0][0]
        self.assertEqual(argv, ['taskkill', '/T', '/F', '/PID', '4242'])

    def test_windows_treats_process_not_found_as_success(self) -> None:
        completed = mock.Mock(returncode=128)
        with mock.patch.object(process_liveness, 'IS_WINDOWS', True), \
                mock.patch.object(
                    process_liveness.subprocess, 'run', return_value=completed,
                ):
            self.assertTrue(process_liveness.kill_process_tree(4242))

    def test_windows_other_exit_codes_report_failure(self) -> None:
        completed = mock.Mock(returncode=1)
        with mock.patch.object(process_liveness, 'IS_WINDOWS', True), \
                mock.patch.object(
                    process_liveness.subprocess, 'run', return_value=completed,
                ):
            self.assertFalse(process_liveness.kill_process_tree(4242))

    def test_windows_taskkill_launch_failure_is_false(self) -> None:
        with mock.patch.object(process_liveness, 'IS_WINDOWS', True), \
                mock.patch.object(
                    process_liveness.subprocess, 'run', side_effect=OSError('no taskkill'),
                ):
            self.assertFalse(
                process_liveness.kill_process_tree(4242, logger=mock.Mock()),
            )

    def test_posix_kills_with_sigkill(self) -> None:
        with mock.patch.object(process_liveness, 'IS_WINDOWS', False), \
                mock.patch.object(process_liveness.os, 'kill') as kill:
            self.assertTrue(process_liveness.kill_process_tree(4242))
        self.assertEqual(kill.call_args[0][0], 4242)

    def test_posix_already_dead_counts_as_success(self) -> None:
        with mock.patch.object(process_liveness, 'IS_WINDOWS', False), \
                mock.patch.object(
                    process_liveness.os, 'kill', side_effect=ProcessLookupError,
                ):
            self.assertTrue(process_liveness.kill_process_tree(4242))

    def test_posix_permission_error_is_failure(self) -> None:
        with mock.patch.object(process_liveness, 'IS_WINDOWS', False), \
                mock.patch.object(
                    process_liveness.os, 'kill', side_effect=PermissionError,
                ):
            self.assertFalse(process_liveness.kill_process_tree(4242))


class PidProbingTests(unittest.TestCase):
    def test_a_bad_pid_is_never_alive(self) -> None:
        with mock.patch.object(process_liveness, 'IS_WINDOWS', False), \
             mock.patch.object(os, 'kill', side_effect=ProcessLookupError):
            self.assertFalse(process_liveness.pid_alive(4242))

    def test_someone_elses_process_counts_as_alive(self) -> None:
        # PermissionError means it exists but is not ours to signal.
        with mock.patch.object(process_liveness, 'IS_WINDOWS', False), \
             mock.patch.object(os, 'kill', side_effect=PermissionError):
            self.assertTrue(process_liveness.pid_alive(4242))

    def test_an_unexpected_os_error_reads_as_dead(self) -> None:
        with mock.patch.object(process_liveness, 'IS_WINDOWS', False), \
             mock.patch.object(os, 'kill', side_effect=OSError):
            self.assertFalse(process_liveness.pid_alive(4242))

    def test_our_own_process_is_alive(self) -> None:
        with mock.patch.object(process_liveness, 'IS_WINDOWS', False):
            self.assertTrue(process_liveness.pid_alive(os.getpid()))


class CoercePidTests(unittest.TestCase):
    def test_junk_from_a_registry_file_is_rejected(self) -> None:
        for junk in (None, '', 'NaN', [], 0, -1, '0'):
            self.assertIsNone(process_liveness.coerce_pid(junk))

    def test_a_numeric_string_is_accepted(self) -> None:
        self.assertEqual(process_liveness.coerce_pid('4242'), 4242)


class ImageNameTests(unittest.TestCase):
    def test_windows_reads_the_first_csv_field(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout='"node.exe","4242","Console"\n',
        )
        with mock.patch.object(process_liveness, 'IS_WINDOWS', True), \
             mock.patch.object(process_liveness.subprocess, 'run', return_value=completed):
            self.assertEqual(process_liveness.image_name(4242), 'node.exe')

    def test_windows_no_such_task_is_unknown(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout='INFO: No tasks are running...\n',
        )
        with mock.patch.object(process_liveness, 'IS_WINDOWS', True), \
             mock.patch.object(process_liveness.subprocess, 'run', return_value=completed):
            self.assertEqual(process_liveness.image_name(4242), '')

    def test_windows_tasklist_failure_is_unknown(self) -> None:
        with mock.patch.object(process_liveness, 'IS_WINDOWS', True), \
             mock.patch.object(process_liveness.subprocess, 'run', side_effect=OSError):
            self.assertEqual(process_liveness.image_name(4242), '')

    def test_posix_unreadable_proc_is_unknown(self) -> None:
        with mock.patch.object(process_liveness, 'IS_WINDOWS', False):
            self.assertEqual(process_liveness.image_name(-1), '')


class KillProcessGroupTests(unittest.TestCase):
    """POSIX must reap what the AGENT started, not just the agent.

    Operator report: a machine crashed with 15-20 node servers still running.
    An agent that runs ``npm run dev`` leaves that server behind — killing the
    CLI does not touch it, because it is a separate process that simply gets
    reparented. Measured with real processes: single-pid kill left the server
    alive indefinitely; a group kill reaped it.

    The guard is the dangerous half. Signalling a group we SHARE would kill
    the orchestrator and every other agent with it, so the group is used only
    when it is not our own.
    """

    def test_a_group_leading_process_has_its_group_signalled(self) -> None:
        with mock.patch.object(process_liveness, 'IS_WINDOWS', False), \
                mock.patch.object(os, 'getpgid', side_effect=[4242, 999]), \
                mock.patch.object(os, 'killpg') as killpg, \
                mock.patch.object(os, 'kill'):
            self.assertTrue(process_liveness.kill_process_tree(4242))
        self.assertEqual(killpg.call_args[0][0], 4242)

    def test_our_OWN_group_is_never_signalled(self) -> None:
        # The suicide guard: same pgid as us => single-pid kill only.
        with mock.patch.object(process_liveness, 'IS_WINDOWS', False), \
                mock.patch.object(os, 'getpgid', return_value=999), \
                mock.patch.object(os, 'killpg') as killpg, \
                mock.patch.object(os, 'kill') as kill:
            self.assertTrue(process_liveness.kill_process_tree(4242))
        killpg.assert_not_called()
        self.assertEqual(kill.call_args[0][0], 4242)

    def test_a_process_in_inits_group_gets_only_the_single_kill(self) -> None:
        with mock.patch.object(process_liveness, 'IS_WINDOWS', False), \
                mock.patch.object(os, 'getpgid', side_effect=[1, 999]), \
                mock.patch.object(os, 'killpg') as killpg, \
                mock.patch.object(os, 'kill') as kill:
            self.assertTrue(process_liveness.kill_process_tree(4242))
        killpg.assert_not_called()
        self.assertEqual(kill.call_args[0][0], 4242)

    def test_an_unresolvable_group_still_kills_the_process(self) -> None:
        # getpgid failing must not stop the kill — the single-pid attempt is
        # what decides the answer.
        with mock.patch.object(process_liveness, 'IS_WINDOWS', False), \
                mock.patch.object(os, 'getpgid', side_effect=OSError('nope')), \
                mock.patch.object(os, 'kill') as kill:
            self.assertTrue(process_liveness.kill_process_tree(4242))
        self.assertEqual(kill.call_args[0][0], 4242)

    def test_windows_does_not_use_groups(self) -> None:
        # taskkill /T already walked the children there.
        with mock.patch.object(process_liveness, 'IS_WINDOWS', True), \
                mock.patch.object(os, 'killpg') as killpg, \
                mock.patch.object(subprocess, 'run') as run:
            run.return_value = mock.Mock(returncode=0)
            self.assertTrue(process_liveness.kill_process_tree(4242))
        killpg.assert_not_called()

    def test_a_real_child_in_its_own_session_is_reaped_with_its_child(self) -> None:
        # No mocks: the behaviour the operator actually cares about.
        agent = subprocess.Popen(
            [
                sys.executable, '-c',
                'import subprocess, sys, time;'
                "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']);"
                'print(p.pid, flush=True); time.sleep(60)',
            ],
            stdout=subprocess.PIPE, text=True, start_new_session=True,
        )
        self.addCleanup(_force_kill, agent)
        server_pid = int(agent.stdout.readline().strip())
        self.addCleanup(_force_kill_pid, server_pid)

        self.assertTrue(process_liveness.kill_process_tree(agent.pid))
        agent.wait(timeout=5)
        deadline = time.monotonic() + 5
        while process_liveness.pid_alive(server_pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertFalse(
            process_liveness.pid_alive(server_pid),
            'the server the agent started outlived the agent',
        )


def _force_kill(proc) -> None:
    try:
        proc.kill()
        proc.wait(timeout=2)
    except Exception:
        pass


def _force_kill_pid(pid: int) -> None:
    try:
        os.kill(pid, getattr(signal, 'SIGKILL', signal.SIGTERM))
    except OSError:
        pass


if __name__ == '__main__':
    unittest.main()
