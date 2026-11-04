"""Coverage tests for the small ``kato_core_lib.data_layers.data.*`` types.

Targets the previously-uncovered repr/eq/edge-case lines so a future
refactor that breaks them (e.g. accidentally returning ``NotImplemented``
from ``__eq__``) surfaces as a CI failure.
"""

from __future__ import annotations

import unittest

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


if __name__ == '__main__':
    unittest.main()
