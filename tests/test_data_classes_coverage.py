"""Coverage tests for the small ``kato_core_lib.data_layers.data.*`` types.

Targets the previously-uncovered repr/eq/edge-case lines so a future
refactor that breaks them (e.g. accidentally returning ``NotImplemented``
from ``__eq__``) surfaces as a CI failure.
"""

from __future__ import annotations

import unittest

from kato_core_lib.data_layers.data.repository_approval import ApprovalSidecar
from kato_core_lib.data_layers.data.task import Task


class TaskDataClassTests(unittest.TestCase):
    """``kato_core_lib.data_layers.data.task.Task`` (lines 27-32, 34-42).

    Smoke-locks the dunder methods so an attribute-rename refactor
    can't silently break the ``Task == Task`` equality used in
    triage / publishing tests.
    """

    def test_repr_includes_every_constructor_field(self) -> None:
        task = Task(
            id='PROJ-1', summary='do thing', description='body',
            branch_name='feature/proj-1', tags=['urgent'],
        )
        rendered = repr(task)
        self.assertIn('PROJ-1', rendered)
        self.assertIn('do thing', rendered)
        self.assertIn('feature/proj-1', rendered)
        self.assertIn('urgent', rendered)

    def test_equality_matches_identical_fields(self) -> None:
        a = Task(id='PROJ-1', summary='x')
        b = Task(id='PROJ-1', summary='x')
        self.assertEqual(a, b)

    def test_equality_differs_when_any_field_differs(self) -> None:
        base = Task(id='PROJ-1', summary='x')
        self.assertNotEqual(base, Task(id='PROJ-2', summary='x'))
        self.assertNotEqual(base, Task(id='PROJ-1', summary='y'))

    def test_equality_returns_false_for_non_task_other(self) -> None:
        # Line 36: ``isinstance`` check — non-Task → False (not
        # NotImplemented). Locks the API surface: ``task == 'string'``
        # must not blow up or return NotImplemented.
        task = Task(id='PROJ-1')
        self.assertNotEqual(task, 'PROJ-1')
        self.assertNotEqual(task, None)
        self.assertNotEqual(task, {'id': 'PROJ-1'})


class ApprovalSidecarFromDictTests(unittest.TestCase):
    """``ApprovalSidecar.from_dict`` defensive parsing (line 104).

    The sidecar is loaded from a JSON file the operator (or a stale
    git checkout) can edit by hand. ``from_dict`` must accept any
    shape of payload without crashing — bad shapes degrade to "no
    approvals", not exceptions."""

    def test_non_list_approved_field_degrades_to_empty(self) -> None:
        # Line 104: if ``approved`` isn't a list (operator typed a
        # string), we set ``raw_entries = []`` so the parse continues.
        sidecar = ApprovalSidecar.from_dict({
            'version': 1,
            'approved': 'not a list — operator mistake',
        })
        self.assertEqual(len(sidecar.approved), 0)

    def test_none_payload_returns_empty_sidecar(self) -> None:
        # ``payload = payload or {}`` — passing None must not crash.
        sidecar = ApprovalSidecar.from_dict(None)
        self.assertEqual(len(sidecar.approved), 0)

    def test_dict_entries_without_repository_id_are_skipped(self) -> None:
        # Entries missing repository_id (or having a non-dict shape)
        # are filtered out of the comprehension — defensive parsing
        # for hand-edited sidecars.
        sidecar = ApprovalSidecar.from_dict({
            'approved': [
                {'repository_id': 'repo-a', 'sha': 'abc'},
                {},                       # missing repository_id
                {'repository_id': ''},    # empty repository_id
                'not a dict',             # wrong shape
            ],
        })
        self.assertEqual(len(sidecar.approved), 1)
        self.assertEqual(sidecar.approved[0].repository_id, 'repo-a')


if __name__ == '__main__':
    unittest.main()
