"""What the proposer is asked, and what a step's task is told."""

from __future__ import annotations

import unittest

from plan_queue_core_lib.plan_queue_core_lib.data.plan import Plan, Step
from plan_queue_core_lib.plan_queue_core_lib.graph import StepDraft
from plan_queue_core_lib.plan_queue_core_lib.proposer_prompt import (
    SPLIT_CLOSE,
    SPLIT_OPEN,
    build_propose_prompt,
)
from plan_queue_core_lib.plan_queue_core_lib.step_text import (
    MAX_DESCRIPTION_CHARS,
    compose_step_description,
)


class ProposerPromptTests(unittest.TestCase):
    def test_a_first_proposal(self) -> None:
        prompt = build_propose_prompt(
            plan_title='Discounts', wrapped_plan='<untrusted>PLAN</untrusted>',
            repositories=['api', 'web'], guidance='  Host rule.  ',
        )
        self.assertIn('"Discounts"', prompt)
        self.assertIn('<untrusted>PLAN</untrusted>', prompt)
        self.assertIn('Repositories a task may work in: api, web.', prompt)
        self.assertIn('Host rule.', prompt)
        self.assertTrue(prompt.rstrip().endswith(SPLIT_CLOSE))
        self.assertEqual(prompt.count(SPLIT_OPEN), 2)  # the instruction and the example
        self.assertNotIn('previous split', prompt)
        self.assertNotIn('could not be used', prompt)

    def test_a_revision_carries_the_previous_split_and_the_feedback(self) -> None:
        prompt = build_propose_prompt(
            plan_title='P', wrapped_plan='X', repositories=['api'],
            previous_steps=[StepDraft('a', 'T', 'B', ('z',), ('api',))],
            feedback='  merge a and b  ', problem='  step 1 has no brief  ',
        )
        self.assertIn('Your previous split was:', prompt)
        self.assertIn('"depends_on": [\n        "z"\n      ]', prompt)
        self.assertIn('merge a and b', prompt)
        self.assertIn('Your last reply could not be used: step 1 has no brief', prompt)
        self.assertNotIn('Host rule', prompt)


class StepDescriptionTests(unittest.TestCase):
    def _plan(self) -> Plan:
        plan = Plan.new('SRC-9', plan_markdown='x', repository_ids=['api'], now=0, title='Discounts')
        plan.steps = [
            Step(id='a', title='Endpoint', brief='Add it.', task_id='T-1'),
            Step(id='b', title='Field', brief='Use it.', depends_on=['a', 'gone']),
            Step(id='c', title='Docs', brief='Write them.'),
        ]
        return plan

    def test_a_step_is_told_its_place_its_parents_and_the_later_steps(self) -> None:
        plan = self._plan()
        text = compose_step_description(plan, plan.step('b'))
        self.assertTrue(text.startswith('Step 2 of 3 of the plan "Discounts" (split from SRC-9).'))
        self.assertIn('Use it.', text)
        self.assertIn('- T-1: Endpoint', text)     # a parent with a task shows its id
        self.assertNotIn('gone', text)              # an unknown parent is skipped
        self.assertIn('leave them alone', text)
        self.assertIn('- Docs', text)
        self.assertIn('done by the operator', text)

    def test_the_last_step_has_no_later_steps_and_a_first_no_parents(self) -> None:
        plan = self._plan()
        last = compose_step_description(plan, plan.step('c'))
        self.assertNotIn('Later steps', last)
        self.assertNotIn('Builds on', compose_step_description(plan, plan.step('a')))

    def test_a_huge_brief_is_cut_to_the_limit(self) -> None:
        plan = self._plan()
        plan.steps[0].brief = 'x' * (MAX_DESCRIPTION_CHARS * 2)
        text = compose_step_description(plan, plan.step('a'))
        self.assertEqual(len(text), MAX_DESCRIPTION_CHARS)
        self.assertTrue(text.endswith('…'))


if __name__ == '__main__':
    unittest.main()
