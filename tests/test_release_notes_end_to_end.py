"""End to end: "Finish & forget" with done ticked puts the task in the release notes.

The real route (``DELETE /api/sessions/<id>/workspace?done=1``), a real
AgentService, the real LocalAwareTaskStateService over a real local-task
store, a real workspace on disk and the real release-notes service writing a
real file. Only the model is a stand-in.

The order matters and is what this pins: the note is written while the
workspace still exists (its title, repositories and the agent's
``pr_description.md`` are read), and the route then deletes the workspace.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from kato_core_lib.data_layers.data_access.task_data_access import TaskDataAccess
from kato_core_lib.data_layers.service.local_task_routing import LocalAwareTaskStateService
from kato_core_lib.data_layers.service.release_note_links import ReleaseNoteLinks
from kato_core_lib.data_layers.service.release_note_sources import ReleaseNoteSources
from kato_core_lib.data_layers.service.release_note_writer import ReleaseNoteWriter
from kato_core_lib.data_layers.service.release_notes_service import ReleaseNotesService
from kato_webserver.app import _build_fallback_manager, create_app
from local_task_core_lib.local_task_core_lib.data.local_task import LocalTaskState
from local_task_core_lib.local_task_core_lib.store import LocalTaskStore
from tests.chaos_lib import build_real_agent_service, materialize_workspace
from tests.test_local_task_routing import DONE, PROGRESS, REVIEW, _Tracker


class FinishAndForgetAddsToReleaseNotesTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        env = patch.dict(os.environ, {'KATO_FORGOTTEN_TASKS_PATH': str(root / 'forgotten.json')})
        env.start()
        self.addCleanup(env.stop)
        self.notes = root / 'release-notes.md'
        self.agent, self.workspaces = build_real_agent_service(root / 'workspaces')
        store = LocalTaskStore(root / 'local_tasks.json')
        self.task_id = store.create('Set a password button', 'details', state=LocalTaskState.REVIEW).id
        materialize_workspace(self.workspaces, self.task_id, summary='Set a password button',
                              repository_ids=['backend'])
        (Path(self.workspaces.workspace_path(self.task_id)) / 'pr_description.md').write_text(
            'Adds POST /set-password and the profile button.', encoding='utf-8',
        )
        self.model = MagicMock(return_value='Clients can now set a password from their profile')
        notes = ReleaseNotesService(
            sources=ReleaseNoteSources(workspace_manager=self.workspaces, task_service=MagicMock()),
            writer=ReleaseNoteWriter(one_shot=self.model, logger=MagicMock()),
            links=ReleaseNoteLinks(pull_requests=None),
            background=lambda work: work(),
            today=lambda: '2026-10-21',
            enabled=lambda: True,
            path=lambda: self.notes,
            workspaces=lambda: root / 'workspaces',
        )
        config = MagicMock(
            progress_state=PROGRESS, review_state=REVIEW, done_state=DONE,
            progress_state_field='State', review_state_field='State',
            done_state_field='State', open_state_field='State',
        )
        self.agent._task_state_service = LocalAwareTaskStateService(
            config, TaskDataAccess(config, _Tracker({})), store, done_observer=notes,
        )
        self.store = store
        self.client = create_app(
            session_manager=_build_fallback_manager(str(root / 'sessions')),
            workspace_manager=self.workspaces,
            agent_service=self.agent,
            fallback_state_dir=str(root / 'sessions'),
        ).test_client()

    def _finish_and_forget(self):
        return self.client.delete(f'/api/sessions/{self.task_id}/workspace?done=1')

    def test_the_task_is_done_forgotten_and_in_the_release_notes(self) -> None:
        response = self._finish_and_forget()

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self.store.get(self.task_id).state, LocalTaskState.DONE)
        self.assertFalse(Path(self.workspaces.workspace_path(self.task_id)).exists())
        notes = self.notes.read_text(encoding='utf-8')
        self.assertIn('## Unreleased\n\n### backend\n\n', notes)
        self.assertIn(
            f'- 2026-10-21 — Clients can now set a password from their profile ({self.task_id})', notes,
        )
        # The model was given what the agent wrote, read before the workspace went.
        self.assertIn('Adds POST /set-password', self.model.call_args.args[0])

    def test_a_failing_model_still_leaves_the_dated_title_line(self) -> None:
        self.model.side_effect = RuntimeError('claude not installed')
        self.assertEqual(self._finish_and_forget().status_code, 200)
        self.assertIn(f'- 2026-10-21 — Set a password button ({self.task_id})', self.notes.read_text(encoding='utf-8'))

    def test_forgetting_without_done_adds_nothing(self) -> None:
        response = self.client.delete(f'/api/sessions/{self.task_id}/workspace')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.notes.exists())


if __name__ == '__main__':
    unittest.main()
