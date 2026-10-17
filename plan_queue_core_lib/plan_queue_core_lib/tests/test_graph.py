"""The one validator every split goes through, and step ordering."""

from __future__ import annotations

import unittest

from plan_queue_core_lib.plan_queue_core_lib.graph import (
    MAX_BRIEF_CHARS,
    MAX_STEPS,
    MAX_TITLE_CHARS,
    SplitValidationError,
    StepDraft,
    dependents_of,
    topological_order,
    validate_split,
)

REPOS = ['api', 'web']


def _d(step_id, depends_on=(), repositories=(), title='Title', brief='Brief') -> StepDraft:
    return StepDraft(step_id, title, brief, tuple(depends_on), tuple(repositories))


class ValidateSplitTests(unittest.TestCase):
    def _refused(self, drafts, message, repos=REPOS) -> None:
        with self.assertRaises(SplitValidationError) as caught:
            validate_split(drafts, allowed_repositories=repos)
        self.assertIn(message, str(caught.exception))

    def test_a_good_split_is_normalised(self) -> None:
        steps = validate_split(
            [_d('api', repositories=['api'], title='  T  ', brief=' b '),
             _d('ui', depends_on=['api', 'api'])],
            allowed_repositories=REPOS,
        )
        self.assertEqual(steps[0], StepDraft('api', 'T', 'b', (), ('api',)))
        # duplicate dependency dropped; no repositories means every allowed one
        self.assertEqual(steps[1].depends_on, ('api',))
        self.assertEqual(steps[1].repositories, ('api', 'web'))

    def test_a_diamond_is_fine(self) -> None:
        drafts = [_d('a'), _d('b', ['a']), _d('c', ['a']), _d('d', ['b', 'c'])]
        self.assertEqual(len(validate_split(drafts, allowed_repositories=REPOS)), 4)

    def test_every_rule_refuses_with_a_reason(self) -> None:
        self._refused([_d('a')], 'no repositories', repos=[])
        self._refused([], 'no steps')
        self._refused([_d(f's{i}') for i in range(MAX_STEPS + 1)], f'at most {MAX_STEPS}')
        for bad in ('', 'Upper', '1st', 'has space', 'x' * 25):
            self._refused([_d(bad)], 'is not usable')
        self._refused([_d('a'), _d('a')], 'used twice')
        self._refused([_d('a', title='  ')], 'has no title')
        self._refused([_d('a', title='t' * (MAX_TITLE_CHARS + 1))], 'title over')
        self._refused([_d('a', brief='')], 'has no brief')
        self._refused([_d('a', brief='b' * (MAX_BRIEF_CHARS + 1))], 'brief over')
        self._refused([_d('a', ['a'])], 'depends on itself')
        self._refused([_d('a', ['zz'])], "unknown step 'zz'")
        self._refused([_d('a', repositories=['other'])], "repository 'other'")
        self._refused([_d('a', ['b']), _d('b', ['a'])], 'in a circle: a, b')
        self._refused([_d('a'), _d('b', ['c']), _d('c', ['d']), _d('d', ['b'])], 'circle: b, c, d')

    def test_repeated_repositories_are_kept_once(self) -> None:
        [step] = validate_split([_d('a', repositories=['api', 'api'])], allowed_repositories=REPOS)
        self.assertEqual(step.repositories, ('api',))


class OrderTests(unittest.TestCase):
    def test_parents_come_first_and_ties_keep_the_listed_order(self) -> None:
        drafts = [_d('d', ['b', 'c']), _d('c', ['a']), _d('b', ['a']), _d('a'), _d('e')]
        self.assertEqual(topological_order(drafts), ['a', 'e', 'c', 'b', 'd'])

    def test_dependents_are_found_through_other_steps(self) -> None:
        drafts = [_d('a'), _d('b', ['a']), _d('c', ['b']), _d('x')]
        self.assertEqual(dependents_of('a', drafts), {'b', 'c'})
        self.assertEqual(dependents_of('x', drafts), set())


if __name__ == '__main__':
    unittest.main()
