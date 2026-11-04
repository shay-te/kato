"""Agents that were working when kato stopped carry on when it starts again.

What must hold:
- "working" is a live chat mid-turn, or waiting for the operator inside its turn;
- the list on disk stays current while kato runs (a kill that cannot be caught
  leaves the last write), and a graceful stop writes it once more BEFORE the
  agents stop, then never again (the teardown is not "nothing was running");
- after the boot each remembered chat is told to continue — through the shared
  chat channel — unless its task was deleted, finished, or has no conversation;
- agents remembered but not resumed yet are kept, so a second stop during the
  boot still remembers them.
"""

from __future__ import annotations

import json
import os
import signal
import tempfile
import threading
import time
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, call, patch

from review_loop_core_lib.review_loop_core_lib.ports import ChatReadiness

from kato_core_lib import main as main_module
from kato_core_lib.data_layers.service.restart_resume.recorder import RunningAgentsRecorder
from kato_core_lib.data_layers.service.restart_resume.resume_store import RESUME_PATH_ENV_KEY, ResumeStore
from kato_core_lib.data_layers.service.restart_resume.resumer import RESUME_PROMPT, RESUMED, AgentResumer
from kato_core_lib.data_layers.service.restart_resume.running_agents import running_task_ids
from kato_core_lib.data_layers.service.restart_resume.wiring import start_restart_resume


class _Session(object):
    def __init__(self, *, alive=True, working=False, waiting=''):
        self.is_alive = alive
        self.is_working = working
        self._waiting = waiting

    def pending_control_request_tool(self):
        if isinstance(self._waiting, Exception):
            raise self._waiting
        return self._waiting


class _Sessions(object):
    """A session manager: records, live sessions, and the chats' ids."""

    def __init__(self, sessions: dict, chats: dict | None = None):
        self.sessions = sessions
        self.chats = chats or {}

    def list_records(self):
        return [SimpleNamespace(task_id=task_id) for task_id in [*self.sessions, '']]

    def get_session(self, task_id):
        session = self.sessions.get(task_id)
        if isinstance(session, Exception):
            raise session
        return session

    def get_record(self, task_id):
        return SimpleNamespace(agent_session_id=self.chats.get(task_id, ''))


class RunningAgentsTests(unittest.TestCase):
    def test_working_or_waiting_inside_its_turn(self) -> None:
        sessions = _Sessions({
            'UNA-1': _Session(working=True),
            'UNA-2': _Session(waiting='Bash'),
            'UNA-3': _Session(),                       # idle
            'UNA-4': _Session(alive=False, working=True),
            'UNA-5': None,
            'UNA-6': RuntimeError('gone'),
            'UNA-7': _Session(waiting=RuntimeError('x')),
        })
        self.assertEqual(running_task_ids(sessions), ['UNA-1', 'UNA-2'])

    def test_no_manager_or_one_that_cannot_list(self) -> None:
        self.assertEqual(running_task_ids(None), [])
        self.assertEqual(running_task_ids(SimpleNamespace(list_records=Mock(side_effect=OSError('x')))), [])
        no_reader = _Sessions({'UNA-1': SimpleNamespace(is_alive=True, is_working=False)})
        self.assertEqual(running_task_ids(no_reader), [])


class ResumeStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / 'resume_agents.json'
        self.store = ResumeStore(self.path, clock=lambda: 42.0)

    def test_written_and_read_back(self) -> None:
        self.assertEqual(self.store.read(), [])
        self.assertTrue(self.store.write(['UNA-2', 'UNA-1', 'UNA-2']))
        self.assertEqual(json.loads(self.path.read_text()), {'task_ids': ['UNA-1', 'UNA-2'], 'recorded_at': 42.0})
        self.assertEqual(self.store.read(), ['UNA-1', 'UNA-2'])

    def test_a_file_that_cannot_be_read_is_nothing_to_resume(self) -> None:
        for text in ('not json', '[1]', '{"task_ids": "UNA-1"}'):
            self.path.write_text(text)
            self.assertEqual(self.store.read(), [], text)
        self.path.write_text('{"task_ids": ["UNA-1", 7, " ", "UNA-1 "]}')
        self.assertEqual(self.store.read(), ['UNA-1'])

    def test_the_default_path_is_under_kato_home_and_can_be_moved(self) -> None:
        with patch.dict(os.environ, {RESUME_PATH_ENV_KEY: str(self.path)}):
            self.assertEqual(ResumeStore().path, self.path)


class RecorderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sessions = _Sessions({'UNA-1': _Session(working=True)})
        self.store = Mock()

    def recorder(self, **kwargs):
        return RunningAgentsRecorder(session_manager=self.sessions, store=self.store, interval=0.01, **kwargs)

    def test_written_only_when_the_working_set_changes(self) -> None:
        recorder = self.recorder()
        self.assertEqual(recorder.record(), ['UNA-1'])
        recorder.record()
        self.sessions.sessions['UNA-2'] = _Session(working=True)
        recorder.record()
        self.sessions.sessions.clear()
        recorder.record()
        self.assertEqual(self.store.write.call_args_list, [call(['UNA-1']), call(['UNA-1', 'UNA-2']), call([])])

    def test_a_graceful_stop_writes_once_more_then_never_again(self) -> None:
        recorder = self.recorder()
        recorder.record()
        self.assertEqual(recorder.record_final(), ['UNA-1'])  # written again even though unchanged
        self.sessions.sessions.clear()                        # the teardown stops every agent...
        self.assertEqual(recorder.record(), ['UNA-1'])        # ...and that is never recorded
        self.assertEqual(recorder.record_final(), ['UNA-1'])
        self.assertEqual(self.store.write.call_count, 2)

    def test_remembered_agents_are_kept_until_they_were_dealt_with(self) -> None:
        recorder = self.recorder(pending=['UNA-9'])
        self.sessions.sessions.clear()
        self.assertEqual(recorder.record(), ['UNA-9'])
        recorder.resumed('UNA-9')
        self.assertEqual(recorder.record(), [])

    def test_the_thread_records_at_once_and_on_its_interval(self) -> None:
        recorder = self.recorder()
        recorder.start()
        recorder.start()  # once
        deadline = time.monotonic() + 2
        while self.store.write.call_count < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.sessions.sessions['UNA-2'] = _Session(working=True)
        while self.store.write.call_count < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        recorder.stop()
        self.assertEqual(self.store.write.call_args_list[:2], [call(['UNA-1']), call(['UNA-1', 'UNA-2'])])

    def test_a_failing_write_is_logged_and_the_thread_goes_on(self) -> None:
        logger = Mock()
        self.store.write.side_effect = [None, OSError('disk full'), None, None, None, None]
        recorder = self.recorder(logger=logger)
        recorder.record()
        self.sessions.sessions.clear()
        recorder.start()
        deadline = time.monotonic() + 2
        while not logger.exception.called and time.monotonic() < deadline:
            time.sleep(0.01)
        recorder.stop()
        logger.exception.assert_called_with('could not record the working agents')


class _Channel(object):
    def __init__(self, readiness=None, delivers=True):
        self.readiness_answer = readiness or ChatReadiness.ready()
        self.delivers = delivers
        self.sent = []

    def dispatch_lock(self, task_id):
        return nullcontext()

    def readiness(self, task_id):
        return self.readiness_answer

    def deliver(self, task_id, prompt, *, force_respawn):
        if isinstance(self.delivers, Exception):
            raise self.delivers
        self.sent.append((task_id, prompt, force_respawn))
        return self.delivers


class ResumerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workspaces = {
            'UNA-1': SimpleNamespace(status='active'), 'UNA-2': SimpleNamespace(status='done'),
            'UNA-3': SimpleNamespace(status='review'), 'UNA-4': SimpleNamespace(status='terminated'),
            'UNA-5': SimpleNamespace(status='active'),
        }
        self.sessions = _Sessions({}, chats={'UNA-1': 'chat-1', 'UNA-3': 'chat-3', 'UNA-6': 'chat-6'})
        self.channel = _Channel()
        self.logger = Mock()
        self.resumer = AgentResumer(
            channel=self.channel, session_manager=self.sessions,
            workspace_manager=SimpleNamespace(get=self.workspaces.get),
            forgotten=lambda task_id: task_id == 'UNA-6', logger=self.logger,
        )

    def test_each_remembered_chat_is_told_to_continue_unless_it_should_not(self) -> None:
        done = []
        outcomes = self.resumer.resume(['UNA-1', 'UNA-2', 'UNA-3', 'UNA-4', 'UNA-5', 'UNA-6', 'UNA-7'], on_done=done.append)
        self.assertEqual(outcomes, {
            'UNA-1': RESUMED, 'UNA-2': 'it is done now', 'UNA-3': RESUMED, 'UNA-4': 'it is terminated now',
            'UNA-5': 'it has no conversation to continue', 'UNA-6': 'it was deleted since',
            'UNA-7': 'its workspace is gone',
        })
        self.assertEqual(self.channel.sent, [('UNA-1', RESUME_PROMPT, False), ('UNA-3', RESUME_PROMPT, False)])
        self.assertEqual(done, ['UNA-1', 'UNA-2', 'UNA-3', 'UNA-4', 'UNA-5', 'UNA-6', 'UNA-7'])
        self.assertTrue(RESUME_PROMPT.startswith('Kato — resumed after a restart'))
        self.assertIn('told it to continue', self.logger.info.call_args_list[0].args[0])

    def test_a_chat_already_running_again_or_that_will_not_start(self) -> None:
        self.channel.readiness_answer = ChatReadiness.wait('kato is running this task in the background')
        self.assertEqual(self.resumer.resume(['UNA-1'])['UNA-1'],
                         'it is not resumed: kato is running this task in the background')
        self.channel.readiness_answer = ChatReadiness.ready()
        self.channel.delivers = False
        self.assertEqual(self.resumer.resume(['UNA-1'])['UNA-1'], 'its chat could not be started')
        self.channel.delivers = RuntimeError('cli missing')
        self.assertEqual(self.resumer.resume(['UNA-1'])['UNA-1'], 'it could not be resumed: cli missing')

    def test_no_workspaces_or_sessions_resumes_nothing(self) -> None:
        resumer = AgentResumer(channel=self.channel, session_manager=None, workspace_manager=None,
                               forgotten=lambda task_id: False)
        self.assertEqual(resumer.resume(['UNA-1']), {'UNA-1': 'its workspace is gone'})
        resumer = AgentResumer(channel=self.channel, session_manager=None,
                               workspace_manager=SimpleNamespace(get=self.workspaces.get), forgotten=lambda t: False)
        self.assertEqual(resumer.resume(['UNA-1']), {'UNA-1': 'it has no conversation to continue'})


class WiringTests(unittest.TestCase):
    def test_read_then_resumed_then_kept_current(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        store = ResumeStore(Path(folder.name) / 'resume.json')
        store.write(['UNA-1'])
        sessions = _Sessions({'UNA-1': None}, chats={'UNA-1': 'chat-1'})
        delivery = MagicMock()
        delivery.runner_in_flight.return_value = False
        delivery.has_busy_turn.return_value = False
        delivery.dispatch_lock_for.return_value = nullcontext()
        delivery.deliver.return_value = True
        comment_runs = SimpleNamespace(chat_delivery=delivery, has_local_comment_pending=lambda task_id: False)
        workspaces = SimpleNamespace(get=lambda task_id: SimpleNamespace(status='active'))
        later = []
        recorder = start_restart_resume(
            session_manager=sessions, workspace_manager=workspaces, comment_runs=comment_runs,
            store=store, run_later=later.append,
        )
        self.assertFalse(delivery.deliver.called)  # off the boot's path
        with patch('kato_core_lib.data_layers.service.restart_resume.resumer.is_forgotten', return_value=False), \
                patch.object(recorder, 'start') as start:
            # The default ``forgotten`` was bound at construction; read live here.
            later.pop()()
        start.assert_called_once()
        task_id, prompt = delivery.deliver.call_args.args
        self.assertEqual((task_id, prompt), ('UNA-1', RESUME_PROMPT))
        self.assertEqual(delivery.deliver.call_args.kwargs['label'], 'resume after restart')
        self.assertEqual(recorder.record(), [])  # dealt with: no longer kept

    def test_nothing_remembered_only_starts_the_recorder(self) -> None:
        store = Mock()
        store.read.return_value = []
        recorder = start_restart_resume(
            session_manager=None, workspace_manager=None, comment_runs=None, store=store,
            run_later=lambda work: work(),
        )
        recorder.stop()
        self.assertIsNotNone(recorder._thread)


class MainWiringTests(unittest.TestCase):
    def test_boot_starts_it_and_a_failure_never_stops_the_boot(self) -> None:
        app = SimpleNamespace(service=SimpleNamespace(comment_runs='runs'), session_manager='s',
                              workspace_manager='w', logger=Mock())
        with patch('kato_core_lib.data_layers.service.restart_resume.wiring.start_restart_resume',
                   return_value='recorder') as start:
            main_module._start_restart_resume(app)
        self.assertEqual(app.running_agents_recorder, 'recorder')
        self.assertEqual(start.call_args.kwargs['comment_runs'], 'runs')
        with patch('kato_core_lib.data_layers.service.restart_resume.wiring.start_restart_resume',
                   side_effect=RuntimeError('x')):
            main_module._start_restart_resume(app)
        app.logger.exception.assert_called_once()
        bare = SimpleNamespace(service=None)
        main_module._start_restart_resume(bare)
        self.assertFalse(hasattr(bare, 'running_agents_recorder'))

    def test_a_graceful_stop_records_before_any_agent_is_stopped(self) -> None:
        order = Mock()
        app = SimpleNamespace(service=order.service, logger=MagicMock(), running_agents_recorder=order.recorder)
        original = signal.getsignal(signal.SIGINT)
        try:
            main_module._register_shutdown_hook(app)
            with patch.object(main_module, '_exit_now'):
                signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
        finally:
            signal.signal(signal.SIGINT, original)
        names = [entry[0] for entry in order.mock_calls]
        self.assertLess(names.index('recorder.record_final'), names.index('service.shutdown'))

    def test_a_failing_final_record_never_blocks_the_stop(self) -> None:
        service = Mock()
        recorder = Mock()
        recorder.record_final.side_effect = OSError('disk full')
        app = SimpleNamespace(service=service, logger=MagicMock(), running_agents_recorder=recorder)
        original = signal.getsignal(signal.SIGINT)
        try:
            main_module._register_shutdown_hook(app)
            with patch.object(main_module, '_exit_now'):
                signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
        finally:
            signal.signal(signal.SIGINT, original)
        service.shutdown.assert_called_once()
        app.logger.exception.assert_any_call('could not record the working agents')


if __name__ == '__main__':
    unittest.main()
