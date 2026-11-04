"""End to end: "Finish & forget" with done ticked posts the task to Slack.

The real route (``DELETE /api/sessions/<id>/workspace?done=1``), a real
AgentService, the real LocalAwareTaskStateService over a real local-task store,
the real done fan-out (release notes AND Slack), a real workspace on disk, and
the real Slack client talking HTTP to a local stand-in for Slack. Only the pull
request lookup and the release-notes model are stand-ins.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from slack_core_lib.slack_core_lib.client.slack_client import SlackClient
from slack_core_lib.slack_core_lib.tests.fakes import FakeSlack

from kato_core_lib.data_layers.data_access.task_data_access import TaskDataAccess
from kato_core_lib.data_layers.service.done_announcements import DoneAnnouncements
from kato_core_lib.data_layers.service.local_task_routing import LocalAwareTaskStateService
from kato_core_lib.data_layers.service.release_note_links import ReleaseNoteLinks
from kato_core_lib.data_layers.service.release_note_sources import ReleaseNoteSources
from kato_core_lib.data_layers.service.release_note_writer import ReleaseNoteWriter
from kato_core_lib.data_layers.service.release_notes_service import ReleaseNotesService
from kato_core_lib.data_layers.service.slack_done_announcer import SlackDoneAnnouncer
from kato_core_lib.helpers.slack_done_settings_utils import SlackDoneSettings
from kato_webserver.app import _build_fallback_manager, create_app
from local_task_core_lib.local_task_core_lib.data.local_task import LocalTaskState
from local_task_core_lib.local_task_core_lib.store import LocalTaskStore
from plan_queue_core_lib.plan_queue_core_lib.ports import PullRequestFact
from tests.chaos_lib import build_real_agent_service, materialize_workspace
from tests.test_local_task_routing import DONE, PROGRESS, REVIEW, _Tracker


class FinishAndForgetPostsToSlackTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        env = patch.dict(os.environ, {'KATO_FORGOTTEN_TASKS_PATH': str(root / 'forgotten.json')})
        env.start()
        self.addCleanup(env.stop)
        self.agent, self.workspaces = build_real_agent_service(root / 'workspaces')
        store = LocalTaskStore(root / 'local_tasks.json')
        self.task_id = store.create('Set a password button', 'details', state=LocalTaskState.REVIEW).id
        materialize_workspace(self.workspaces, self.task_id, summary='Set a password button',
                              repository_ids=['backend', 'client'])
        self.slack = FakeSlack()
        self.addCleanup(self.slack.close)
        prs = {'backend': 'https://git.example/backend/pull/7', 'client': 'https://git.example/client/pull/8'}
        links = ReleaseNoteLinks(pull_requests=lambda branch, repo, **_: [
            PullRequestFact(state='merged', url=prs[repo], created_at=''),
        ])
        sources = ReleaseNoteSources(workspace_manager=self.workspaces, task_service=MagicMock())
        self.notes = root / 'release-notes.md'
        release_notes = ReleaseNotesService(
            sources=sources, writer=ReleaseNoteWriter(one_shot=MagicMock(return_value='A password button'),
                                                      logger=MagicMock()),
            links=links, background=lambda work: work(), today=lambda: '2026-11-04',
            enabled=lambda: True, path=lambda: self.notes, workspaces=lambda: root / 'workspaces',
        )
        self.settings = SlackDoneSettings(channel_link='https://acme.slack.com/archives/C0RELEASE1', token='xoxb-1')
        slack = SlackDoneAnnouncer(
            sources=sources, links=links, settings=lambda: self.settings,
            client_for=lambda token: SlackClient(token, base_url=self.slack.url),
            background=lambda work: work(),
        )
        config = MagicMock(
            progress_state=PROGRESS, review_state=REVIEW, done_state=DONE,
            progress_state_field='State', review_state_field='State',
            done_state_field='State', open_state_field='State',
        )
        self.agent._task_state_service = LocalAwareTaskStateService(
            config, TaskDataAccess(config, _Tracker({})), store,
            done_observer=DoneAnnouncements([release_notes, slack]),
        )
        self.store = store
        self.client = create_app(
            session_manager=_build_fallback_manager(str(root / 'sessions')),
            workspace_manager=self.workspaces, agent_service=self.agent,
            fallback_state_dir=str(root / 'sessions'),
        ).test_client()

    def test_done_posts_the_task_and_its_pull_requests_and_writes_the_notes(self) -> None:
        response = self.client.delete(f'/api/sessions/{self.task_id}/workspace?done=1')

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self.store.get(self.task_id).state, LocalTaskState.DONE)
        [request] = self.slack.requests
        self.assertEqual(request['body']['channel'], 'C0RELEASE1')
        self.assertEqual(request['body']['text'], '\n'.join([
            f'✅ *{self.task_id}* is done — Set a password button',
            '• <https://git.example/backend/pull/7|backend>',
            '• <https://git.example/client/pull/8|client>',
        ]))
        self.assertIn('A password button', self.notes.read_text(encoding='utf-8'))  # both were told

    def test_a_slack_that_refuses_never_fails_the_done(self) -> None:
        self.slack.answer = (200, {'ok': False, 'error': 'invalid_auth'})
        response = self.client.delete(f'/api/sessions/{self.task_id}/workspace?done=1')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.store.get(self.task_id).state, LocalTaskState.DONE)

    def test_forgetting_without_done_posts_nothing(self) -> None:
        self.assertEqual(self.client.delete(f'/api/sessions/{self.task_id}/workspace').status_code, 200)
        self.assertEqual(self.slack.requests, [])


if __name__ == '__main__':
    unittest.main()
