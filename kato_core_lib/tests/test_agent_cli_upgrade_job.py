"""Tests for the background agent-CLI upgrade job (progress + outcome)."""
import time
import unittest
from unittest import mock

from kato_core_lib.helpers import agent_cli_upgrade_job as job

_ALLOWED_PLAN = {
    'allowed': True, 'reason': '', 'manager': 'npm',
    'argv': ['/usr/bin/npm', 'install', '-g', 'pkg@latest'],
    'command': 'npm install -g pkg@latest',
}


class FakeProcess:
    """A Popen stand-in whose output the job streams line by line."""

    def __init__(self, lines, code=0):
        self.stdout = iter(f'{line}\n' for line in lines)
        self._code = code
        self.killed = False

    def wait(self, timeout=None):
        return self._code

    def kill(self):
        self.killed = True


def _spawner(lines, code=0):
    return lambda argv: FakeProcess(lines, code)


def _wait_for_finish(timeout=5.0):
    """Block until the worker thread leaves the running state."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not job.is_running():
            return job.status()
        time.sleep(0.01)
    raise AssertionError('upgrade job did not finish')


class UpgradeJobTests(unittest.TestCase):
    def setUp(self):
        job.reset()
        self.addCleanup(job.reset)
        plan = mock.patch.object(job, 'upgrade_plan', return_value=dict(_ALLOWED_PLAN))
        plan.start()
        self.addCleanup(plan.stop)
        installed = mock.patch.object(job, 'installed_version', return_value='2.1.179')
        installed.start()
        self.addCleanup(installed.stop)
        # The job resets the published-version cache on success; keep that off
        # the network in tests.
        reset_cache = mock.patch.object(job, 'reset_latest_version_cache')
        reset_cache.start()
        self.addCleanup(reset_cache.stop)

    def test_idle_before_anything_runs(self):
        status = job.status()
        self.assertEqual(status['state'], 'idle')
        self.assertEqual(status['percent'], 0)
        self.assertFalse(job.is_running())

    def test_successful_run_reaches_done_at_100(self):
        job.start(env={}, spawner=_spawner([
            'npm http fetch GET 200 https://registry.npmjs.org/pkg',
            'added 1 package in 3s',
        ]), verifier=lambda env: '2.1.222')
        status = _wait_for_finish()
        self.assertEqual(status['state'], 'done')
        self.assertTrue(status['ok'])
        self.assertEqual(status['percent'], 100)
        self.assertEqual(status['version_before'], '2.1.179')
        self.assertEqual(status['version_after'], '2.1.222')
        self.assertIn('2.1.179 → 2.1.222', status['message'])

    def test_output_is_captured_for_the_log_tail(self):
        job.start(env={}, spawner=_spawner(['first line', 'second line']),
                  verifier=lambda env: '2.1.222')
        status = _wait_for_finish()
        self.assertEqual(status['lines'], ['first line', 'second line'])

    def test_log_tail_is_capped(self):
        noisy = [f'line {n}' for n in range(job._MAX_LINES + 50)]
        job.start(env={}, spawner=_spawner(noisy), verifier=lambda env: '2.1.222')
        status = _wait_for_finish()
        self.assertEqual(len(status['lines']), job._MAX_LINES)
        self.assertEqual(status['lines'][-1], noisy[-1])  # kept the NEWEST

    def test_nonzero_exit_is_an_error_with_the_output(self):
        job.start(env={}, spawner=_spawner(['npm ERR! EACCES'], code=1),
                  verifier=lambda env: '2.1.222')
        status = _wait_for_finish()
        self.assertEqual(status['state'], 'error')
        self.assertFalse(status['ok'])
        self.assertIn('code 1', status['message'])
        self.assertIn('npm ERR! EACCES', status['lines'])
        self.assertNotEqual(status['percent'], 100)  # never "complete" on failure

    def test_success_without_a_readable_version_is_not_reported_as_upgraded(self):
        # The command exiting 0 is not proof the new binary works — telling the
        # operator "upgraded" then leaving a CLI that answers nothing is the
        # false-success shape this guards.
        job.start(env={}, spawner=_spawner(['added 1 package']),
                  verifier=lambda env: None)
        status = _wait_for_finish()
        self.assertEqual(status['state'], 'error')
        self.assertIn('did not report a version', status['message'])

    def test_a_silently_hanging_command_is_killed_by_the_watchdog(self):
        # The failure this guards: `for line in stream` BLOCKS, so a deadline
        # checked inside the read loop never fires for a command that hangs
        # while printing nothing. Only an out-of-band kill unblocks it.
        killed = __import__('threading').Event()

        class HangingProcess:
            def __init__(self):
                self._released = __import__('threading').Event()
                self.stdout = self._lines()

            def _lines(self):
                # A generator, so the block happens on ITERATION (inside the
                # read loop) — which is where the real hang occurs.
                self._released.wait(5)  # produces nothing until killed
                yield from ()

            def wait(self, timeout=None):
                return -9

            def kill(self):
                killed.set()
                self._released.set()

        with mock.patch.object(job, '_TIMEOUT_SECONDS', 0.05):
            job.start(env={}, spawner=lambda argv: HangingProcess(),
                      verifier=lambda env: '2.1.222')
            status = _wait_for_finish()
        self.assertTrue(killed.is_set())
        self.assertEqual(status['state'], 'error')
        self.assertIn('timed out', status['message'])

    def test_spawn_failure_is_reported_not_raised(self):
        def boom(argv):
            raise OSError('no such file')

        job.start(env={}, spawner=boom, verifier=lambda env: '2.1.222')
        status = _wait_for_finish()
        self.assertEqual(status['state'], 'error')
        self.assertIn('no such file', status['message'])

    def test_blocked_plan_reports_the_reason_without_running(self):
        with mock.patch.object(job, 'upgrade_plan', return_value={
            'allowed': False, 'reason': 'in-app upgrade is disabled',
            'manager': '', 'argv': [], 'command': '',
        }):
            snapshot = job.start(env={}, spawner=_spawner(['should not run']))
        self.assertEqual(snapshot['state'], 'error')
        self.assertIn('disabled', snapshot['message'])
        self.assertFalse(job.is_running())

    def test_second_start_does_not_disturb_the_running_job(self):
        release = __import__('threading').Event()

        def slow_spawner(argv):
            release.wait(5)
            return FakeProcess(['added 1 package'])

        job.start(env={}, spawner=slow_spawner, verifier=lambda env: '2.1.222')
        second = job.start(env={}, spawner=_spawner(['different run']))
        self.assertTrue(second.get('already_running'))
        self.assertEqual(second['state'], 'running')
        release.set()
        status = _wait_for_finish()
        self.assertEqual(status['lines'], ['added 1 package'])  # the FIRST run's

    def test_snapshot_never_leaks_internal_progress_keys(self):
        job.start(env={}, spawner=_spawner(['added 1 package']),
                  verifier=lambda env: '2.1.222')
        status = _wait_for_finish()
        self.assertEqual([k for k in status if k.startswith('_')], [])


class ProgressTests(unittest.TestCase):
    """The bar's honesty rules."""

    def setUp(self):
        job.reset()
        self.addCleanup(job.reset)

    def _running_at(self, floor, floor_age_seconds):
        with job._lock:
            job._state.update(state='running', percent=floor)
            job._state['_floor'] = floor
            job._state['_floor_at'] = time.time() - floor_age_seconds

    def test_milestones_advance_the_floor(self):
        with job._lock:
            job._state.update(state='running', percent=3, lines=[])
            job._state['_floor'] = 3
            job._state['_floor_at'] = time.time()
        job._record_line('npm http fetch GET 200 https://registry.npmjs.org/x')
        with job._lock:
            self.assertEqual(job._state['_floor'], 30)
            self.assertEqual(job._state['step'], 'Downloading…')
        job._record_line('added 1 package in 2s')
        with job._lock:
            self.assertEqual(job._state['_floor'], 85)

    def test_a_late_earlier_milestone_never_moves_the_bar_backwards(self):
        with job._lock:
            job._state.update(state='running', percent=85, lines=[])
            job._state['_floor'] = 85
            job._state['_floor_at'] = time.time()
        job._record_line('npm http fetch GET 200 https://registry.npmjs.org/x')
        with job._lock:
            self.assertEqual(job._state['_floor'], 85)

    def test_creep_moves_but_never_reaches_the_next_milestone(self):
        self._running_at(30, 0.0)
        fresh = job.status()['percent']
        self._running_at(30, 8.0)
        later = job.status()['percent']
        self.assertGreater(later, fresh)
        self.assertLess(later, 60)  # the next milestone's floor

    def test_creep_is_bounded_even_after_a_very_long_wait(self):
        self._running_at(85, 3600.0)
        self.assertLess(job.status()['percent'], job._RUNNING_CEILING)

    def test_creep_never_applies_to_a_finished_job(self):
        with job._lock:
            job._state.update(state='done', percent=100, ok=True)
        self.assertEqual(job.status()['percent'], 100)


if __name__ == '__main__':
    unittest.main()
