"""Adding a finished task to the release notes — in two steps, never lost.

Real workspace service, real files, the background step run inline. The model
and the git host are the only stand-ins.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from kato_core_lib.data_layers.service.release_note_links import ReleaseNoteLinks
from kato_core_lib.data_layers.service.release_note_sources import ReleaseNoteSources
from kato_core_lib.data_layers.service.release_note_writer import ReleaseNoteWriter
from kato_core_lib.data_layers.service.release_notes_service import ReleaseNotesService
from kato_core_lib.helpers.release_notes_file_utils import read_release_notes
from tests.chaos_lib import build_real_workspace_service, materialize_workspace


class ReleaseNotesServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.workspaces = build_real_workspace_service(root / 'workspaces')
        self.notes = root / 'notes' / 'release-notes.md'
        self.model = MagicMock(return_value='Clients can now set a password')
        self.pull_requests = MagicMock(return_value=[])
        self.after_write = MagicMock()
        self.deferred: list = []
        self.enabled = True
        self.day = '2026-10-21'
        materialize_workspace(self.workspaces, 'UNA-1', summary='Set a password button',
                              repository_ids=['backend'])

    def _service(self, *, inline=True):
        return ReleaseNotesService(
            sources=ReleaseNoteSources(workspace_manager=self.workspaces, task_service=MagicMock()),
            writer=ReleaseNoteWriter(one_shot=self.model, logger=MagicMock()),
            links=ReleaseNoteLinks(pull_requests=self.pull_requests, logger=MagicMock()),
            after_write=self.after_write,
            background=(lambda work: work()) if inline else self.deferred.append,
            today=lambda: self.day,
            enabled=lambda: self.enabled,
            path=lambda: self.notes,
            workspaces=lambda: Path(self._tmp.name) / 'workspaces',
            logger=MagicMock(),
        )

    def _notes(self) -> str:
        return read_release_notes(self.notes)

    def test_a_done_task_gets_a_dated_line_in_the_model_s_words(self) -> None:
        self._service().task_done('UNA-1')
        self.assertIn('### backend', self._notes())
        self.assertIn(
            '- 2026-10-21 — Clients can now set a password (UNA-1) <!-- kato:task UNA-1 -->', self._notes(),
        )
        self.assertNotIn('Set a password button', self._notes())

    def test_the_title_line_is_written_at_once_and_survives_if_the_rest_never_runs(self) -> None:
        service = self._service(inline=False)
        service.task_done('UNA-1')
        # Kato stopped before the background step: the task is still in the notes.
        self.assertIn('- 2026-10-21 — Set a password button (UNA-1)', self._notes())
        self.model.assert_not_called()
        self.deferred[0]()
        self.assertIn('Clients can now set a password', self._notes())

    def test_the_background_step_keeps_the_date_it_was_done_on(self) -> None:
        service = self._service(inline=False)
        service.task_done('UNA-1')
        self.day = '2026-10-22'  # midnight passed before the model answered
        self.deferred[0]()
        self.assertIn('- 2026-10-21 — Clients can now set a password', self._notes())

    def test_a_failing_model_leaves_the_title_line(self) -> None:
        self.model.side_effect = RuntimeError('timed out')
        self._service().task_done('UNA-1')
        self.assertIn('- 2026-10-21 — Set a password button (UNA-1)', self._notes())

    def test_pull_request_links_are_added(self) -> None:
        from types import SimpleNamespace
        self.pull_requests.return_value = [SimpleNamespace(state='merged', url='https://git.example/pr/7', created_at='')]
        self._service().task_done('UNA-1')
        self.assertIn('· [PR](https://git.example/pr/7)', self._notes())

    def test_an_operator_edit_made_meanwhile_is_kept(self) -> None:
        service = self._service(inline=False)
        service.task_done('UNA-1')
        self.notes.write_text(self._notes().replace('Set a password button', 'My own words'), encoding='utf-8')
        self.deferred[0]()
        self.assertIn('My own words', self._notes())
        self.assertNotIn('Clients can now set a password', self._notes())

    def test_the_same_task_done_twice_is_one_entry(self) -> None:
        service = self._service()
        service.task_done('UNA-1')
        service.task_done('UNA-1')
        self.assertEqual(self._notes().count('<!-- kato:task UNA-1 -->'), 1)
        self.assertEqual(self.model.call_count, 1)

    def test_switched_off_writes_nothing(self) -> None:
        self.enabled = False
        self._service().task_done('UNA-1')
        self.assertFalse(self.notes.exists())

    def test_a_file_inside_the_workspaces_folder_is_refused(self) -> None:
        self.notes = Path(self._tmp.name) / 'workspaces' / 'UNA-1' / 'notes.md'
        service = self._service()
        service.task_done('UNA-1')
        self.assertFalse(self.notes.exists())
        service.logger.warning.assert_called_once()

    def test_each_write_is_followed_by_the_follow_ups(self) -> None:
        self._service().task_done('UNA-1')
        self.assertEqual(self.after_write.call_count, 2)  # the title line, then the better line

    def test_a_failing_follow_up_never_breaks_the_note(self) -> None:
        self.after_write.side_effect = RuntimeError('google down')
        self._service().task_done('UNA-1')
        self.assertIn('Clients can now set a password', self._notes())

    def test_nothing_ever_raises_into_the_done_action(self) -> None:
        service = self._service()
        service._sources = MagicMock(read=MagicMock(side_effect=RuntimeError('disk gone')))
        service.task_done('UNA-1')  # no exception
        service.logger.exception.assert_called_once()
        service.task_done('   ')  # an empty id is ignored


if __name__ == '__main__':
    unittest.main()
