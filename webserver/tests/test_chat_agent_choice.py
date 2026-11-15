"""A new task's chat starts on the agent the operator picked.

Quick start lets the operator set up Codex, Grok or OpenCode instead of Claude
Code; a task then has to begin on that agent, never on one they do not have. A
chat is routed by the agent on its record, so the record is written first —
the same thing switching a task to another agent does. A model picked from the
default agent's list (a Claude name) must not reach the other agent.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from kato_core_lib.helpers import agent_backend_readiness, agent_version_utils, chat_agent_utils
from kato_webserver import local_task_starter
from kato_webserver.app import _new_task_chat_agent
from kato_webserver.chat_agent_choice import start_on_chosen_agent


class FakeRouter(object):
    """The routing session manager's record and backend face."""

    def __init__(self, *, available=('claude', 'codex', 'grok', 'opencode'), default='claude', records=None):
        self._available = list(available)
        self._default = default
        self.records = dict(records or {})

    def available_backends(self):
        return list(self._available)

    def default_backend(self):
        return self._default

    def get_record(self, task_id):
        return self.records.get(task_id)

    def save_record(self, record):
        self.records[record.task_id] = record


class StartOnChosenAgentTests(unittest.TestCase):
    def test_a_new_task_gets_a_record_on_the_chosen_agent(self) -> None:
        router = FakeRouter()
        self.assertTrue(start_on_chosen_agent(router, ' LOCAL-1 ', 'codex', task_summary='Fix the login'))
        record = router.records['LOCAL-1']
        self.assertEqual((record.agent_backend, record.task_summary, record.agent_session_id), ('codex', 'Fix the login', ''))

    def test_nothing_is_written_when_there_is_nothing_to_choose(self) -> None:
        cases = {
            'no pick': (FakeRouter(), ''),
            'the default already': (FakeRouter(), 'claude'),
            'not wired here': (FakeRouter(available=('claude',)), 'grok'),
            'a chat under way': (FakeRouter(records={'LOCAL-1': SimpleNamespace(agent_backend='claude')}), 'codex'),
        }
        for name, (router, agent) in cases.items():
            with self.subTest(case=name):
                before = dict(router.records)
                self.assertFalse(start_on_chosen_agent(router, 'LOCAL-1', agent))
                self.assertEqual(router.records, before)

    def test_a_manager_without_the_routing_face_is_left_alone(self) -> None:
        self.assertFalse(start_on_chosen_agent(None, 'LOCAL-1', 'codex'))
        self.assertFalse(start_on_chosen_agent(SimpleNamespace(get_record=lambda task_id: None), 'LOCAL-1', 'codex'))
        no_default = SimpleNamespace(available_backends=lambda: ['codex'], get_record=lambda task_id: None,
                                     save_record=mock.Mock())
        self.assertTrue(start_on_chosen_agent(no_default, 'LOCAL-1', 'codex'))
        no_default.save_record.assert_called_once()


class LocalTaskStarterTests(unittest.TestCase):
    def starter(self, router):
        app = SimpleNamespace(config={'SESSION_MANAGER': router}, logger=mock.Mock())
        self.overrides = {}
        self.modes = {}
        starter = local_task_starter.LocalTaskStarter(
            app,
            set_mode=lambda task_id, mode: self.modes.__setitem__(task_id, mode) or True,
            set_override=lambda key, task_id, value: self.overrides.__setitem__(key, value) or True,
            start_chat=mock.Mock(),
            run_in_background=lambda work, name: None,  # the clone + first message are not under test
        )
        return starter

    def start(self, router, agent: str) -> None:
        with mock.patch.object(local_task_starter, 'chat_agent', return_value=agent), \
                mock.patch.object(local_task_starter, '_persist_mode'):
            self.starter(router).start('LOCAL-1', 'plan', model='opus', effort='high')

    def test_on_the_chosen_agent_the_default_agents_model_and_effort_are_dropped(self) -> None:
        router = FakeRouter()
        self.start(router, 'codex')
        self.assertEqual(router.records['LOCAL-1'].agent_backend, 'codex')
        self.assertEqual(self.overrides, {'TASK_MODEL_OVERRIDES': '', 'TASK_EFFORT_OVERRIDES': ''})

    def test_on_the_default_agent_the_picked_model_and_effort_apply(self) -> None:
        router = FakeRouter()
        self.start(router, '')
        self.assertEqual(router.records, {})
        self.assertEqual(self.overrides, {'TASK_MODEL_OVERRIDES': 'opus', 'TASK_EFFORT_OVERRIDES': 'high'})


class ChatAgentSettingTests(unittest.TestCase):
    def test_only_a_chat_agent_kato_knows_counts(self) -> None:
        for value, expected in (('Codex ', 'codex'), ('grok', 'grok'), ('openhands', ''), ('', ''), (None, '')):
            with self.subTest(value=value):
                self.assertEqual(chat_agent_utils.normalized_chat_agent(value), expected)
        with mock.patch.object(chat_agent_utils, 'live_setting', return_value='OpenCode'):
            self.assertEqual(chat_agent_utils.chat_agent(), 'opencode')

    def test_the_new_task_screen_hears_where_a_task_would_start(self) -> None:
        def app(router):
            return SimpleNamespace(config={'SESSION_MANAGER': router})
        with mock.patch.object(chat_agent_utils, 'live_setting', return_value='codex'):
            self.assertEqual(_new_task_chat_agent(app(FakeRouter())), 'codex')
            self.assertEqual(_new_task_chat_agent(app(FakeRouter(available=('claude',)))), '')
            self.assertEqual(_new_task_chat_agent(app(FakeRouter(default='codex'))), '')
            self.assertEqual(_new_task_chat_agent(app(None)), '')
        with mock.patch.object(chat_agent_utils, 'live_setting', return_value=''):
            self.assertEqual(_new_task_chat_agent(app(FakeRouter())), '')


class ReadinessAndDownloadTests(unittest.TestCase):
    def test_readiness_is_read_fresh_in_the_clis_own_words(self) -> None:
        missing = mock.Mock()
        missing.validate_connection.side_effect = RuntimeError('Codex CLI ("codex") was not found on PATH.')
        with mock.patch.object(agent_backend_readiness, '_build_probe_client', return_value=missing):
            self.assertIn('was not found on PATH', agent_backend_readiness.readiness_error('codex', 'codex'))
        ready = mock.Mock()
        with mock.patch.object(agent_backend_readiness, '_build_probe_client', return_value=ready):
            self.assertEqual(agent_backend_readiness.readiness_error('codex'), '')
        silent = mock.Mock()
        silent.validate_connection.side_effect = RuntimeError('')
        with mock.patch.object(agent_backend_readiness, '_build_probe_client', return_value=silent):
            self.assertEqual(agent_backend_readiness.readiness_error('codex'), 'RuntimeError')
        self.assertIn('unknown agent backend', agent_backend_readiness.readiness_error('nope'))

    def test_each_cli_has_a_download_page(self) -> None:
        self.assertEqual(agent_version_utils.download_page('claude'), 'https://code.claude.com/docs/en/setup')
        self.assertEqual(agent_version_utils.download_page('grok'), 'https://x.ai/cli')
        self.assertEqual(agent_version_utils.download_page('openhands'), '')


if __name__ == '__main__':
    unittest.main()
