"""Strict git off: the webserver's three jobs.

1. Kato's git buttons (push, pull, merge, update source, pull request, approve,
   Done) answer 409 while a LOCAL-git agent is mid-turn — two writers on one
   index and HEAD otherwise. A strict session mid-turn is not blocked: there
   the agent never commits or moves HEAD, and pressing Push mid-turn has
   always been allowed.
2. The Action Guard judges the agent's git at the level its session was
   spawned at (``session.git_access``), not at a guess.
3. A running chat whose deny list no longer matches the setting is respawned
   on its next message — that is how flipping the switch reaches it.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from kato_webserver import app as app_module
from kato_webserver.agent_git_busy import AGENT_GIT_BUSY_MESSAGE, agent_git_busy_reason
from kato_webserver.app import create_app

_KEY = 'KATO_AGENT_GIT_STRICT'

# (route, agent-service method, what that method returns on success)
_GIT_ROUTES = (
    ('/api/sessions/T-1/push', 'push_task', {'pushed': True}),
    ('/api/sessions/T-1/pull', 'pull_task', {'pulled': True}),
    ('/api/sessions/T-1/merge-default-branch', 'merge_default_branch_for_task', {'merged': True}),
    ('/api/sessions/T-1/pull-request', 'create_pull_request_for_task', {'created': True}),
    ('/api/sessions/T-1/update-source', 'update_source_for_task', {'updated': True}),
    ('/api/sessions/T-1/approve-push', 'approve_push', {'approved': True}),
    ('/api/sessions/T-1/finish', 'finish_task_planning_session', {'finished': True}),
)


def _session(*, git_access='local', working=True, alive=True, **extra):
    return SimpleNamespace(
        git_access=git_access, is_working=working, is_alive=alive, **extra,
    )


class _Manager:
    def __init__(self, session) -> None:
        self._session = session

    def get_session(self, task_id):  # noqa: ARG002
        return self._session

    def list_records(self):
        return []

    def get_record(self, task_id):  # noqa: ARG002
        return None


class BusyReasonTests(unittest.TestCase):
    def test_only_a_live_local_session_mid_turn_is_busy(self) -> None:
        self.assertEqual(agent_git_busy_reason(_Manager(_session()), 'T-1'), AGENT_GIT_BUSY_MESSAGE)
        for session in (
            _session(git_access='strict'),
            _session(git_access=''),
            _session(working=False),
            _session(alive=False),
            SimpleNamespace(is_working=True, is_alive=True),  # Codex / older object
            None,
        ):
            with self.subTest(session=session):
                self.assertEqual(agent_git_busy_reason(_Manager(session), 'T-1'), '')

    def test_no_manager_or_a_failing_lookup_never_blocks(self) -> None:
        self.assertEqual(agent_git_busy_reason(None, 'T-1'), '')
        broken = MagicMock()
        broken.get_session.side_effect = RuntimeError('boom')
        self.assertEqual(agent_git_busy_reason(broken, 'T-1'), '')


class GitButtonRouteTests(unittest.TestCase):
    def _post(self, session, route, method, result):
        calls = []

        def run(*args, **kwargs):
            calls.append((args, kwargs))
            return result

        agent = SimpleNamespace(**{method: run})
        response = create_app(
            session_manager=_Manager(session), agent_service=agent,
        ).test_client().post(route)
        return response, calls

    def test_every_git_button_waits_for_a_local_git_turn(self) -> None:
        for route, method, result in _GIT_ROUTES:
            with self.subTest(route=route):
                response, calls = self._post(_session(), route, method, result)
                self.assertEqual(response.status_code, 409)
                body = response.get_json()
                self.assertTrue(body['agent_busy'])
                self.assertIn('turn ends', body['error'])
                self.assertEqual(calls, [], 'kato ran git while the agent might be')

    def test_a_strict_session_mid_turn_is_not_blocked(self) -> None:
        for route, method, result in _GIT_ROUTES:
            with self.subTest(route=route):
                response, calls = self._post(_session(git_access='strict'), route, method, result)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(len(calls), 1)

    def test_a_local_session_between_turns_is_not_blocked(self) -> None:
        for route, method, result in _GIT_ROUTES:
            with self.subTest(route=route):
                response, calls = self._post(_session(working=False), route, method, result)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(len(calls), 1)


class GuardReadsTheSessionLevelTests(unittest.TestCase):
    def _verdict(self, session, command):
        return app_module._classify_action_for(session, 'Bash', {'command': command})

    def test_a_local_session_may_commit_and_still_may_not_push(self) -> None:
        session = _session(cwd='')
        self.assertEqual(self._verdict(session, 'git commit -m x').decision.value, 'allow')
        self.assertEqual(self._verdict(session, 'git push').decision.value, 'block')

    def test_a_session_with_no_level_is_strict(self) -> None:
        for session in (_session(git_access='strict', cwd=''), SimpleNamespace(cwd='')):
            with self.subTest(session=session):
                verdict = self._verdict(session, 'git commit -m x')
                self.assertEqual(verdict.decision.value, 'block')
                self.assertEqual(verdict.rule_id, 'git.orchestrator_owned')


class RespawnOnSwitchTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.settings_path = Path(tmp.name) / 'settings.json'
        patcher = patch.dict(os.environ, {'KATO_SETTINGS_FILE': str(self.settings_path)})
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop(_KEY, None)

    def _strict_off(self) -> None:
        self.settings_path.write_text(json.dumps({_KEY: 'false'}), encoding='utf-8')

    @staticmethod
    def _floor(**kwargs) -> str:
        from claude_core_lib.claude_core_lib.cli_client import ClaudeCliClient
        return ClaudeCliClient._merge_disallowed_with_floor('', **kwargs)

    def _needs_respawn(self, disallowed, *, mode='acceptEdits') -> bool:
        flask_app = MagicMock()
        flask_app.config = {}
        session = SimpleNamespace(
            disallowed_tools=disallowed, is_alive=True, is_working=False, permission_mode=mode,
        )
        return app_module._tool_floor_change_needs_respawn(
            flask_app, _Manager(session), 'T-1', None,
        )

    def test_switching_strict_off_respawns_a_strict_chat(self) -> None:
        self.assertFalse(self._needs_respawn(self._floor()))
        self._strict_off()
        self.assertTrue(self._needs_respawn(self._floor()))

    def test_switching_it_back_on_respawns_a_local_chat(self) -> None:
        local = self._floor(git_access='local')
        self._strict_off()
        self.assertFalse(self._needs_respawn(local))
        self.settings_path.write_text(json.dumps({_KEY: 'true'}), encoding='utf-8')
        self.assertTrue(self._needs_respawn(local))

    def test_an_auto_mode_chat_is_unaffected_by_the_switch(self) -> None:
        auto = self._floor(bypass_permissions=True)
        self._strict_off()
        self.assertFalse(self._needs_respawn(auto, mode='bypassPermissions'))


if __name__ == '__main__':
    unittest.main()
