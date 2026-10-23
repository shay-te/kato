"""What a release note is written from, and the pull requests it links.

Sources: the workspace kato kept (real files), or — with none on this machine —
the tracker. Links: merged before open, never declined, never an older task's.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from kato_core_lib.data_layers.data.release_note_source import ReleaseNoteSource
from kato_core_lib.data_layers.service.release_note_links import ReleaseNoteLinks
from kato_core_lib.data_layers.service.release_note_sources import ReleaseNoteSources
from kato_core_lib.helpers.release_notes_file_utils import read_release_notes, update_release_notes
from tests.chaos_lib import build_real_workspace_service, materialize_workspace


class ReleaseNoteSourcesTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspaces = build_real_workspace_service(Path(self._tmp.name))
        self.tracker = MagicMock()

    def _sources(self):
        return ReleaseNoteSources(
            workspace_manager=self.workspaces, task_service=self.tracker, logger=MagicMock(),
        )

    def test_the_workspace_and_the_agent_s_pr_description_are_read(self) -> None:
        materialize_workspace(self.workspaces, 'UNA-1', summary='Set a password',
                              repository_ids=['backend', 'client'])
        folder = Path(self.workspaces.workspace_path('UNA-1'))
        (folder / 'pr_description.md').write_text('Adds the button.', encoding='utf-8')

        source = self._sources().read('UNA-1')

        self.assertEqual(source.title, 'Set a password')
        self.assertEqual(source.repositories, ('backend', 'client'))
        self.assertEqual(source.pr_description, 'Adds the button.')
        self.assertGreater(source.started_at, 0)
        self.tracker.get_task.assert_not_called()

    def test_no_workspace_here_means_the_tracker_is_asked(self) -> None:
        self.tracker.get_task.return_value = SimpleNamespace(summary='From the ticket', description='D')
        source = self._sources().read('UNA-9')
        self.assertEqual((source.title, source.description, source.repositories), ('From the ticket', 'D', ()))

    def test_nothing_known_anywhere_still_gives_the_id(self) -> None:
        self.tracker.get_task.side_effect = RuntimeError('tracker down')
        source = self._sources().read('UNA-9')
        self.assertEqual(source.fallback, 'UNA-9')


class ReleaseNoteLinksTests(unittest.TestCase):
    SOURCE = ReleaseNoteSource(task_id='UNA-1', repositories=('backend',), started_at=1_790_000_000.0)

    @staticmethod
    def _fact(state, url, created_at=''):
        return SimpleNamespace(state=state, url=url, created_at=created_at)

    def test_merged_wins_over_open_and_declined_is_never_linked(self) -> None:
        links = ReleaseNoteLinks(pull_requests=lambda task, repo: [
            self._fact('declined', 'https://x/1'), self._fact('open', 'https://x/2'),
            self._fact('merged', 'https://x/3'),
        ], logger=MagicMock())
        self.assertEqual(links.links(self.SOURCE), [('PR', 'https://x/3')])
        only_declined = ReleaseNoteLinks(pull_requests=lambda t, r: [self._fact('declined', 'https://x/1')])
        self.assertEqual(only_declined.links(self.SOURCE), [])

    def test_a_pull_request_from_before_the_task_started_is_another_task_s(self) -> None:
        links = ReleaseNoteLinks(pull_requests=lambda task, repo: [
            self._fact('merged', 'https://x/old', '2020-01-01T00:00:00Z'),
            self._fact('open', 'https://x/new', '2026-10-01T00:00:00Z'),
        ])
        self.assertEqual(links.links(self.SOURCE), [('PR', 'https://x/new')])

    def test_several_repositories_are_labelled_by_repository(self) -> None:
        source = ReleaseNoteSource(task_id='UNA-1', repositories=('backend', 'client'))
        links = ReleaseNoteLinks(pull_requests=lambda task, repo: [self._fact('merged', f'https://x/{repo}')])
        self.assertEqual(links.links(source), [('backend', 'https://x/backend'), ('client', 'https://x/client')])

    def test_a_host_that_cannot_be_asked_just_means_no_link(self) -> None:
        def boom(task, repo):
            raise RuntimeError('401')

        self.assertEqual(ReleaseNoteLinks(pull_requests=boom, logger=MagicMock()).links(self.SOURCE), [])
        self.assertEqual(ReleaseNoteLinks(pull_requests=None).links(self.SOURCE), [])


class ReleaseNotesFileTests(unittest.TestCase):
    def test_a_change_is_written_and_an_unchanged_one_is_not(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'deep' / 'notes.md'
            self.assertEqual(read_release_notes(path), '')
            self.assertTrue(update_release_notes(path, lambda text: text + 'one\n'))
            self.assertEqual(read_release_notes(path), 'one\n')
            self.assertFalse(update_release_notes(path, lambda text: text))


if __name__ == '__main__':
    unittest.main()
