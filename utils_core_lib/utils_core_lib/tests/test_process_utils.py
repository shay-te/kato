"""A launcher in front of a program must leave Ctrl+C to the program.

Out of process on purpose: what is under test is what the TERMINAL does —
one SIGINT to every process in the foreground group — and what each process
does with it. Every test here builds a real process group and signals it the
way a terminal does.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from utils_core_lib.utils_core_lib.process_utils import run_leaving_ctrl_c_to_child

REPO_ROOT = Path(__file__).resolve().parents[3]

# The program: a SIGINT handler with a few seconds of shutdown work, then an
# exit code of its own. Long enough that a 0.25s kill could not miss it.
_PROGRAM = textwrap.dedent('''
    import os, signal, sys, time
    log = open(sys.argv[1], 'a', buffering=1)
    def handler(signum, frame):
        log.write('stopping\\n')
        time.sleep(1.5)
        log.write('stopped\\n')
        os._exit(7)
    signal.signal(signal.SIGINT, handler)
    log.write('ready\\n')
    while True:
        time.sleep(0.1)
''')

# A launcher, run directly or in front of another launcher.
_LAUNCHER = textwrap.dedent('''
    import sys
    sys.path.insert(0, {root!r})
    from utils_core_lib.utils_core_lib.process_utils import run_leaving_ctrl_c_to_child
    sys.exit(run_leaving_ctrl_c_to_child([sys.executable, *sys.argv[1:]]))
''')


@unittest.skipIf(os.name == 'nt', 'process groups and killpg are POSIX')
class CtrlCThroughLaunchersTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        self.log = tmp / 'log.txt'
        self.program = tmp / 'program.py'
        self.program.write_text(_PROGRAM, encoding='utf-8')
        self.launcher = tmp / 'launcher.py'
        self.launcher.write_text(_LAUNCHER.format(root=str(REPO_ROOT)), encoding='utf-8')

    def _start(self, *argv: str) -> subprocess.Popen:
        process = subprocess.Popen(
            [sys.executable, *argv], start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        deadline = time.time() + 10
        while time.time() < deadline:
            if self.log.exists() and 'ready' in self.log.read_text():
                return process
            time.sleep(0.05)
        process.kill()
        self.fail('the program never started')

    def _ctrl_c(self, process: subprocess.Popen) -> float:
        """SIGINT to the whole group, as a terminal does; seconds until exit."""
        started = time.time()
        os.killpg(process.pid, signal.SIGINT)
        process.wait(timeout=15)
        return time.time() - started

    def _program_still_running(self) -> bool:
        found = subprocess.run(
            ['pgrep', '-f', str(self.program)], capture_output=True,
        )
        return found.returncode == 0

    def test_the_program_finishes_its_shutdown_before_the_prompt_returns(self) -> None:
        launcher = self._start(str(self.launcher), str(self.program), str(self.log))
        elapsed = self._ctrl_c(launcher)
        self.assertEqual(self.log.read_text().splitlines(), ['ready', 'stopping', 'stopped'])
        self.assertGreaterEqual(elapsed, 1.4)
        self.assertFalse(self._program_still_running())

    def test_two_launchers_deep_nothing_is_killed_or_left_behind(self) -> None:
        # The shape of the real launch chain: a CLI in front of a launcher in
        # front of the program. With plain ``subprocess`` calls this killed the
        # program mid-shutdown or left it running behind the prompt.
        launcher = self._start(
            str(self.launcher), str(self.launcher), str(self.program), str(self.log),
        )
        self._ctrl_c(launcher)
        self.assertEqual(self.log.read_text().splitlines(), ['ready', 'stopping', 'stopped'])
        self.assertFalse(self._program_still_running())

    def test_the_programs_exit_code_comes_back_through_every_launcher(self) -> None:
        launcher = self._start(
            str(self.launcher), str(self.launcher), str(self.program), str(self.log),
        )
        self._ctrl_c(launcher)
        self.assertEqual(launcher.returncode, 7)

    def test_plain_subprocess_calls_really_do_break_it(self) -> None:
        # The control: the same chain with ``subprocess.call`` in each
        # launcher. If this ever passes, the helper is no longer needed.
        plain = self.launcher.with_name('plain.py')
        plain.write_text(
            'import subprocess, sys\n'
            'sys.exit(subprocess.call([sys.executable, *sys.argv[1:]]))\n',
            encoding='utf-8',
        )
        broken = 0
        for _ in range(3):
            self.log.write_text('')
            launcher = self._start(str(plain), str(plain), str(self.program), str(self.log))
            self._ctrl_c(launcher)
            time.sleep(0.2)
            killed_midway = 'stopped' not in self.log.read_text()
            left_running = self._program_still_running()
            broken += int(killed_midway or left_running)
            subprocess.run(['pkill', '-f', str(self.program)])
            time.sleep(1.6)
        self.assertEqual(broken, 3)


class RunLeavingCtrlCToChildTests(unittest.TestCase):
    def test_returns_the_childs_exit_code(self) -> None:
        code = run_leaving_ctrl_c_to_child([sys.executable, '-c', 'raise SystemExit(5)'])
        self.assertEqual(code, 5)

    def test_passes_popen_options_through(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            code = run_leaving_ctrl_c_to_child(
                [sys.executable, '-c',
                 'import os, sys; sys.exit(0 if os.getcwd() == os.path.realpath(sys.argv[1]) else 1)',
                 cwd],
                cwd=cwd,
            )
        self.assertEqual(code, 0)

    def test_the_previous_handler_is_restored_even_if_waiting_fails(self) -> None:
        before = signal.getsignal(signal.SIGINT)
        child = MagicMock()
        child.wait.side_effect = RuntimeError('wait failed')
        with patch(
            'utils_core_lib.utils_core_lib.process_utils.subprocess.Popen',
            return_value=child,
        ):
            with self.assertRaises(RuntimeError):
                run_leaving_ctrl_c_to_child(['anything'])
        self.assertIs(signal.getsignal(signal.SIGINT), before)

    def test_the_child_is_started_before_sigint_is_ignored(self) -> None:
        # An ignored SIGINT is inherited across exec: a child started after it
        # could never be interrupted at all.
        seen: list[object] = []

        def popen(*_args, **_kwargs):
            seen.append(signal.getsignal(signal.SIGINT))
            child = MagicMock()
            child.wait.return_value = 0
            return child

        with patch('utils_core_lib.utils_core_lib.process_utils.subprocess.Popen', side_effect=popen):
            run_leaving_ctrl_c_to_child(['anything'])
        self.assertIsNot(seen[0], signal.SIG_IGN)


if __name__ == '__main__':
    unittest.main()
