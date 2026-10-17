"""The proposer's reply is read strictly: a usable split or a clear reason."""

from __future__ import annotations

import unittest

from plan_queue_core_lib.plan_queue_core_lib.proposer_prompt import SPLIT_CLOSE, SPLIT_OPEN
from plan_queue_core_lib.plan_queue_core_lib.split import SplitProposalError, parse_task_split
from plan_queue_core_lib.plan_queue_core_lib.tests.fakes import split_reply, step_spec

REPOS = ['api', 'web']


class ParseTaskSplitTests(unittest.TestCase):
    def _parse(self, text):
        return parse_task_split(text, allowed_repositories=REPOS)

    def _refused(self, text, message) -> None:
        with self.assertRaises(SplitProposalError) as caught:
            self._parse(text)
        self.assertIn(message, str(caught.exception))

    def test_reads_steps_and_notes(self) -> None:
        proposal = self._parse(split_reply(
            [step_spec('api', repositories=['api']), step_spec('ui', depends_on=['api'])],
            notes='  two steps  ',
        ))
        self.assertEqual([step.id for step in proposal.steps], ['api', 'ui'])
        self.assertEqual(proposal.steps[1].depends_on, ('api',))
        self.assertEqual(proposal.notes, 'two steps')

    def test_the_last_block_is_the_answer(self) -> None:
        quoted = f'Format:\n{SPLIT_OPEN}\n{{"steps": "example"}}\n{SPLIT_CLOSE}\n'
        proposal = self._parse(quoted + split_reply([step_spec('real')]))
        self.assertEqual(proposal.steps[0].id, 'real')

    def test_a_fenced_json_body_is_accepted(self) -> None:
        body = '{"steps": [{"id": "a", "title": "T", "brief": "B"}]}'
        for fence in (f'```json\n{body}\n```', f'```\n{body}'):
            proposal = self._parse(f'{SPLIT_OPEN}\n{fence}\n{SPLIT_CLOSE}')
            self.assertEqual(proposal.steps[0].repositories, ('api', 'web'))

    def test_odd_notes_are_dropped(self) -> None:
        text = f'{SPLIT_OPEN}{{"steps": [{{"id": "a", "title": "T", "brief": "B"}}], "notes": 5}}{SPLIT_CLOSE}'
        self.assertEqual(self._parse(text).notes, '')

    def test_refusals(self) -> None:
        self._refused('no block at all', 'has no <task-split> block')
        self._refused(f'{SPLIT_OPEN} never closed', 'has no <task-split> block')
        self._refused(f'{SPLIT_OPEN}not json{SPLIT_CLOSE}', 'not valid JSON')
        self._refused(f'{SPLIT_OPEN}[1, 2]{SPLIT_CLOSE}', 'no "steps" list')
        self._refused(f'{SPLIT_OPEN}{{"steps": {{}}}}{SPLIT_CLOSE}', 'no "steps" list')
        self._refused(f'{SPLIT_OPEN}{{"steps": ["x"]}}{SPLIT_CLOSE}', 'step 1 is not an object')
        self._refused(split_reply([{'id': 'a', 'title': 'T'}]), 'step 1 has no text "brief"')
        self._refused(split_reply([{'id': 'a', 'title': 'T', 'brief': 'B', 'depends_on': 'x'}]),
                      '"depends_on" that is not a list')
        self._refused(split_reply([{'id': 'a', 'title': 'T', 'brief': 'B', 'repositories': [1]}]),
                      '"repositories" that is not a list')
        # the validator's own reasons come through unchanged
        self._refused(split_reply([step_spec('a', depends_on=['b'])]), "unknown step 'b'")

    def test_blank_list_entries_are_ignored(self) -> None:
        proposal = self._parse(split_reply([
            {'id': ' a ', 'title': 'T', 'brief': 'B', 'depends_on': [' '], 'repositories': [' api ']},
        ]))
        self.assertEqual(proposal.steps[0].id, 'a')
        self.assertEqual(proposal.steps[0].depends_on, ())
        self.assertEqual(proposal.steps[0].repositories, ('api',))


if __name__ == '__main__':
    unittest.main()
