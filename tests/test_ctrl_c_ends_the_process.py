"""Ctrl+C must END the process, not merely unwind the main thread.

Reported again after the graceful path had already been bounded and given
terminal feedback: "still i can't kill 'kato up' with ctrl+c".

Measured cause: ``ThreadPoolExecutor`` workers are NON-daemon (Python 3.9+)
and ``concurrent.futures`` registers an exit hook that JOINS them. A scan task
in flight — a git clone, a provider call, a whole agent run — therefore holds
the interpreter open after ``SystemExit`` unwinds the main thread, silently,
for as long as that task takes. In a probe: handler finished at 0.5s, process
alive until 12.1s for a 12s worker. So the handler leaves through ``os._exit``
(``main._exit_now``) once its bounded cleanup is done.

Out of process on purpose. ``os._exit`` in the test runner would take the
whole suite with it, and what is under test is precisely what the INTERPRETER
does at teardown — which no in-process mock can answer.
"""
from __future__ import annotations

import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# The worker outlives any legitimate shutdown by far, so "exited quickly"
# cannot be confused with "waited for the task".
_WORKER_SECONDS = 120

_CHILD = '''
import sys, time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

sys.path.insert(0, {root!r})
from kato_core_lib import main as kato_main

pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='kato-task-worker')
pool.submit(time.sleep, {worker})
time.sleep(0.2)  # let the worker actually start

def _quiet(*args, **kwargs):
    return None

app = SimpleNamespace(
    logger=SimpleNamespace(
        info=_quiet, warning=_quiet, exception=_quiet, debug=_quiet,
    ),
    service=SimpleNamespace(shutdown=_quiet),
    resume_prompt_watcher=None,
    comment_run_watcher=None,
)
kato_main._register_shutdown_hook(app)
print('ready', flush=True)
while True:
    time.sleep(0.1)
'''


class CtrlCEndsTheProcessTests(unittest.TestCase):
    def test_a_running_pool_worker_does_not_hold_the_process_open(self) -> None:
        child = subprocess.Popen(
            [
                sys.executable, '-c',
                _CHILD.format(root=str(REPO_ROOT), worker=_WORKER_SECONDS),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        try:
            self.assertEqual(
                (child.stdout.readline() or '').strip(), 'ready',
                'the child never installed the shutdown hook',
            )
            child.send_signal(signal.SIGINT)
            started = time.monotonic()
            try:
                child.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.fail(
                    'kato did not exit on SIGINT while a pool worker was '
                    'running — the reported "cannot Ctrl+C" hang'
                )
            elapsed = time.monotonic() - started
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
            if child.stdout is not None:
                child.stdout.close()

        # Cleanup here is a no-op, so this is the exit itself. Anything near
        # the worker's runtime would mean the process waited for it.
        self.assertLess(
            elapsed, 12.0,
            f'SIGINT took {elapsed:.1f}s with a {_WORKER_SECONDS}s worker running',
        )


_LOGGING_LOCK_CHILD = '''
import logging, sys, threading, time
from types import SimpleNamespace

sys.path.insert(0, {root!r})
from kato_core_lib import main as kato_main

logging.basicConfig(stream=sys.stderr, level=logging.INFO)

# Another thread is mid-write and holds the log handler's lock. kato logs
# from many threads at once (idle spinner, webserver request threads, the
# watchers, agent readers), so a SIGINT landing in this window is ordinary.
handler = logging.getLogger().handlers[0]
def _hog():
    handler.acquire()
    threading.Event().wait()
threading.Thread(target=_hog, daemon=True).start()
time.sleep(0.3)

app = SimpleNamespace(
    logger=logging.getLogger('probe'),
    service=SimpleNamespace(shutdown=lambda: None),
    resume_prompt_watcher=None,
    comment_run_watcher=None,
)
kato_main._register_shutdown_hook(app)
print('ready', flush=True)
while True:
    time.sleep(0.1)
'''


class CtrlCSurvivesAHeldLoggingLockTests(unittest.TestCase):
    """The shutdown path must not wait on a lock another thread can hold.

    This is the "sometimes Ctrl+C works, sometimes it does nothing at all"
    report. The handler used to call ``app.logger.info(...)`` before doing
    anything else, and every logging handler guards itself with a lock. A
    SIGINT arriving while another thread held that lock left the handler
    blocking on it forever — and since the handler never reached its own
    ``os._exit``, pressing Ctrl+C again could not help either.

    Reproduced 5 times out of 5 before the fix: alive 20s after the signal.
    Out of process for the same reason as the test above — what is under
    test is whether the PROCESS ends.
    """

    def test_sigint_exits_even_while_a_log_handler_lock_is_held(self) -> None:
        child = subprocess.Popen(
            [sys.executable, '-c', _LOGGING_LOCK_CHILD.format(root=str(REPO_ROOT))],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        try:
            self.assertEqual(
                (child.stdout.readline() or '').strip(), 'ready',
                'the child never installed the shutdown hook',
            )
            child.send_signal(signal.SIGINT)
            started = time.monotonic()
            try:
                child.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.fail(
                    'kato did not exit on SIGINT while a log handler lock was '
                    'held — the intermittent "Ctrl+C does nothing" hang'
                )
            elapsed = time.monotonic() - started
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
            if child.stdout is not None:
                child.stdout.close()
        # Bounded by the cleanup deadline plus the flush deadline, not by the
        # thread that is never giving the lock back.
        self.assertLess(
            elapsed,
            kato_main_grace() + 6.0,
            f'SIGINT took {elapsed:.1f}s with a log handler lock held',
        )


def kato_main_grace() -> float:
    """``SHUTDOWN_GRACE_SECONDS`` read from the module under test."""
    sys.path.insert(0, str(REPO_ROOT))
    from kato_core_lib import main as kato_main
    return float(kato_main.SHUTDOWN_GRACE_SECONDS)


if __name__ == '__main__':
    unittest.main()
