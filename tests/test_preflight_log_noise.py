"""The chat's workspace-preparation log shows work, not reuse.

Reported after restarting kato on an old 26-repository task: the chat filled with
"cloning 3/26: … (already on disk, reusing)" and "✓ cloned 3/26: …" for every
repository — one of the 26 had actually been cloned — and every restart appended
another fifty lines. "i dont need to see this redundant".
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'webserver'))

from kato_core_lib.data_layers.service.workspace_provisioning_service import (
    provision_task_workspace_clones,
)
from kato_core_lib.helpers.preflight_log_utils import visible_preflight_entries


def _provision(repos, fail=()):
    """``repos`` is ``[(repository_id, already_on_disk)]``. Returns the log
    lines written and the error raised, if any."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / 'T-1'
        paths = {}
        for repository_id, on_disk in repos:
            paths[repository_id] = root / repository_id
            if on_disk:
                (paths[repository_id] / '.git').mkdir(parents=True)
        workspace_service = MagicMock()
        workspace_service.get.return_value = None
        workspace_service.repository_path.side_effect = (
            lambda _task_id, repository_id: paths[repository_id]
        )
        repository_service = MagicMock()

        def ensure_clone(repository, _path):
            if repository.id in fail:
                raise RuntimeError('unreachable')

        repository_service.ensure_clone.side_effect = ensure_clone
        task = SimpleNamespace(id='T-1', summary='s', description='')
        error = None
        try:
            provision_task_workspace_clones(
                workspace_service, repository_service, task,
                [SimpleNamespace(id=repository_id, local_path='') for repository_id, _ in repos],
            )
        except RuntimeError as exc:
            error = exc
    lines = [call.args[1] for call in workspace_service.append_preflight_log.call_args_list]
    return lines, error


class PreparationWritesOnlyRealWorkTests(unittest.TestCase):
    def test_everything_already_on_disk_writes_nothing(self) -> None:
        lines, error = _provision([('a', True), ('b', True), ('c', True)])
        self.assertIsNone(error)
        self.assertEqual(lines, [])

    def test_only_the_repositories_actually_cloned_are_announced(self) -> None:
        lines, error = _provision([('a', True), ('pay-core-lib', False), ('c', True)])
        self.assertIsNone(error)
        self.assertEqual(lines, [
            'preparing workspace: cloning 1 of 3 repository(ies) — 2 already on disk',
            'cloning 1/1: pay-core-lib',
            '✓ cloned 1/1: pay-core-lib',
            '✓ cloned 1 repository(ies) — starting agent',
        ])

    def test_a_fresh_workspace_announces_every_clone(self) -> None:
        lines, _error = _provision([('a', False), ('b', False)])
        self.assertEqual(lines[0], 'preparing workspace: cloning 2 of 2 repository(ies)')
        self.assertIn('cloning 1/2: a', lines)
        self.assertIn('cloning 2/2: b', lines)
        self.assertEqual(lines[-1], '✓ cloned 2 repository(ies) — starting agent')

    def test_a_failure_is_shown_even_for_a_reused_repository(self) -> None:
        lines, error = _provision([('a', True)], fail={'a'})
        self.assertIsNotNone(error)
        self.assertIn('✗ clone failed: a: unreachable', lines)


class OldLogsReplayWithoutReuseNoiseTests(unittest.TestCase):
    def test_a_preparation_that_only_reused_is_dropped_whole(self) -> None:
        entries = [
            (1.0, 'before'),
            (2.0, 'preparing workspace (2 repository(ies))'),
            (2.0, 'cloning 1/2: a (already on disk, reusing)'),
            (2.0, 'cloning 2/2: b (already on disk, reusing)'),
            (3.0, '✓ cloned 2/2: b'),
            (3.0, '✓ cloned 1/2: a'),
            (3.0, '✓ all 2 repository(ies) cloned — starting agent'),
        ]
        self.assertEqual(visible_preflight_entries(entries), [(1.0, 'before')])

    def test_a_mixed_preparation_keeps_only_the_real_clone(self) -> None:
        entries = [
            (1.0, 'preparing workspace (3 repository(ies))'),
            (1.0, 'cloning 1/3: a (already on disk, reusing)'),
            (1.0, 'cloning 2/3: pay-core-lib'),
            (1.0, 'cloning 3/3: c (already on disk, reusing)'),
            (2.0, '✓ cloned 1/3: a'),
            (2.0, '✓ cloned 3/3: c'),
            (3.0, '✓ cloned 2/3: pay-core-lib'),
            (3.0, '✓ all 3 repository(ies) cloned — starting agent'),
        ]
        self.assertEqual([text for _epoch, text in visible_preflight_entries(entries)], [
            'preparing workspace (3 repository(ies))',
            'cloning 2/3: pay-core-lib',
            '✓ cloned 2/3: pay-core-lib',
            '✓ all 3 repository(ies) cloned — starting agent',
        ])

    def test_a_failure_keeps_its_preparation_visible(self) -> None:
        entries = [
            (1.0, 'preparing workspace (1 repository(ies))'),
            (1.0, 'cloning 1/1: a (already on disk, reusing)'),
            (2.0, '✗ clone failed 1/1: a: boom'),
        ]
        self.assertEqual([text for _epoch, text in visible_preflight_entries(entries)], [
            'preparing workspace (1 repository(ies))',
            '✗ clone failed 1/1: a: boom',
        ])

    def test_each_preparation_is_judged_on_its_own(self) -> None:
        entries = [
            (1.0, 'preparing workspace (1 repository(ies))'),
            (1.0, 'cloning 1/1: a (already on disk, reusing)'),
            (1.0, '✓ cloned 1/1: a'),
            (1.0, '✓ all 1 repository(ies) cloned — starting agent'),
            (5.0, 'preparing workspace: cloning 1 of 2 repository(ies) — 1 already on disk'),
            (5.0, 'cloning 1/1: b'),
            (6.0, '✓ cloned 1/1: b'),
            (6.0, '✓ cloned 1 repository(ies) — starting agent'),
        ]
        self.assertEqual(visible_preflight_entries(entries), entries[4:])

    def test_a_log_with_nothing_to_hide_is_unchanged(self) -> None:
        entries = [
            (1.0, 'preparing workspace (1 repository(ies))'),
            (1.0, 'cloning 1/1: a'),
            (2.0, '✓ cloned 1/1: a'),
            (2.0, '✓ all 1 repository(ies) cloned — starting agent'),
        ]
        self.assertEqual(visible_preflight_entries(entries), entries)
        self.assertEqual(visible_preflight_entries([]), [])

    def test_the_chat_replay_uses_the_filter(self) -> None:
        from kato_webserver.app import _replay_preflight_log

        class _Workspace:
            def read_preflight_log(self, _task_id):
                return [
                    (0.5, 'an earlier line, before any preparation'),
                    (1.0, 'preparing workspace (1 repository(ies))'),
                    (1.0, 'cloning 1/1: a (already on disk, reusing)'),
                    (1.0, '✓ cloned 1/1: a'),
                    (1.0, '✓ all 1 repository(ies) cloned — starting agent'),
                ]

        frames = [frame for _epoch, frame in _replay_preflight_log(_Workspace(), 'T-1')]
        self.assertEqual(len(frames), 1)
        self.assertIn('an earlier line, before any preparation', frames[0])


if __name__ == '__main__':
    unittest.main()
