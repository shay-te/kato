"""The plan as text on the tracker: written, read back strictly, and trusted
only so far."""

from __future__ import annotations

import unittest

from plan_queue_core_lib.plan_queue_core_lib.data.plan import Plan, PlanMode, PlanStatus, Step, StepStatus
from plan_queue_core_lib.plan_queue_core_lib.plan_record import acceptable, parse_record, render_record

PLAN_ID = 'a' * 32
HEADER = f'plan {PLAN_ID} for PROJ-1 · status running · driven by host-1 (alice)'


def plan_with_steps() -> Plan:
    plan = Plan(plan_id=PLAN_ID, source_task_id='PROJ-1', mode=PlanMode.TRACKER, status=PlanStatus.RUNNING,
                driver='host-1', driver_user='alice')
    plan.steps = [
        Step(id='api', title='Add | the (endpoint)', brief='b', task_id='PROJ-2', repositories=['api'],
             status=StepStatus.SATISFIED),
        Step(id='ui', title='Use it', brief='b', task_id='PROJ-3', depends_on=['api', 'gone'],
             repositories=['web', 'api']),
        Step(id='later', title='Not created', brief='b', depends_on=['api']),
        Step(id='docs', title='Docs', brief='b', task_id='PROJ-4', depends_on=['later']),
    ]
    return plan


class RenderTests(unittest.TestCase):
    def test_one_line_per_step_that_has_a_task(self) -> None:
        self.assertEqual(render_record(plan_with_steps()).splitlines(), [
            HEADER,
            'PROJ-2 | api | Add / the [endpoint] | in api | waits for — | done',
            'PROJ-3 | ui | Use it | in web, api | waits for PROJ-2',
            'PROJ-4 | docs | Docs | in — | waits for —',  # its parent has no task yet
        ])

    def test_what_is_rendered_reads_back(self) -> None:
        record = parse_record(render_record(plan_with_steps()), author='alice')
        self.assertEqual((record.plan_id, record.source_task_id, record.status, record.driver,
                          record.driver_user, record.author),
                         (PLAN_ID, 'PROJ-1', 'running', 'host-1', 'alice', 'alice'))
        self.assertEqual([(s.task_id, s.step_id, s.title, s.waits_for, s.repositories, s.done) for s in record.steps], [
            ('PROJ-2', 'api', 'Add / the [endpoint]', (), ('api',), True),
            ('PROJ-3', 'ui', 'Use it', ('PROJ-2',), ('web', 'api'), False),
            ('PROJ-4', 'docs', 'Docs', (), (), False),
        ])


class ParseTests(unittest.TestCase):
    def test_a_record_inside_a_comment(self) -> None:
        text = f'The plan, split:\n\n```\n{HEADER}\nPROJ-2 | api | A | in api | waits for —\n```\nThanks'
        record = parse_record(text)
        self.assertEqual([s.task_id for s in record.steps], ['PROJ-2'])
        self.assertEqual(record.author, '')

    def test_blank_lines_before_the_steps_are_skipped_and_the_block_ends_at_the_next(self) -> None:
        text = f'{HEADER}\n\nPROJ-2 | api | A | in api | waits for —\n\nPROJ-9 | x | X | in api | waits for —'
        self.assertEqual([s.step_id for s in parse_record(text).steps], ['api'])

    def test_no_record(self) -> None:
        for text in ('', 'just a comment', 'plan nope for X · status running · driven by h (u)', None):
            self.assertIsNone(parse_record(text))

    def test_a_record_that_does_not_hold_together_is_no_record(self) -> None:
        bad_steps = {
            'no steps': '',
            'a malformed line': 'PROJ-2 | API | A | in api | waits for —',
            'a task twice': 'PROJ-2 | a | A | in api | waits for —\nPROJ-2 | b | B | in api | waits for —',
            'a step twice': 'PROJ-2 | a | A | in api | waits for —\nPROJ-3 | a | B | in api | waits for —',
            'waits for an unknown task': 'PROJ-2 | a | A | in api | waits for PROJ-9',
            'a circle': 'PROJ-2 | a | A | in api | waits for PROJ-3\nPROJ-3 | b | B | in api | waits for PROJ-2',
        }
        for case, steps in bad_steps.items():
            with self.subTest(case):
                self.assertIsNone(parse_record(f'{HEADER}\n{steps}'))


class AcceptTests(unittest.TestCase):
    def _record(self, *, author='alice', steps='PROJ-2 | a | A | in api | waits for —\n'
                'PROJ-3 | b | B | in api | waits for PROJ-2', plan_id=PLAN_ID, user='alice'):
        header = f'plan {plan_id} for PROJ-1 · status running · driven by host-1 ({user})'
        return parse_record(f'{header}\n{steps}', author=author)

    def test_the_driver_posts_its_own_record(self) -> None:
        self.assertEqual(acceptable(self._record(), previous=None), '')
        self.assertIn('posted by mallory, not by its driver alice', acceptable(self._record(author='mallory'), previous=None))
        self.assertIn('posted by nobody', acceptable(self._record(author=''), previous=None))

    def test_a_later_record_of_the_same_plan_keeps_its_order(self) -> None:
        first = self._record()
        recreated = self._record(steps='PROJ-2 | a | A | in api | waits for — | done\n'
                                       'PROJ-7 | b | B | in api | waits for PROJ-2', user='bob', author='bob')
        self.assertEqual(acceptable(recreated, previous=first), '')
        reordered = self._record(steps='PROJ-2 | a | A | in api | waits for —\nPROJ-3 | b | B | in api | waits for —')
        self.assertIn('changes which step waits for which', acceptable(reordered, previous=first))
        another_plan = self._record(plan_id='b' * 32, steps='PROJ-5 | z | Z | in api | waits for —')
        self.assertEqual(acceptable(another_plan, previous=first), '')


if __name__ == '__main__':
    unittest.main()
