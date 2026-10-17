"""A split the operator already wrote as files: one step per file, each file's
text kept exactly as its brief; the proposer only names the steps and works
out who waits for whom and where each works."""

from __future__ import annotations

import json
import tempfile
import unittest

from plan_queue_core_lib.plan_queue_core_lib.data.plan import Plan, PlanStatus, SplitFile, Step
from plan_queue_core_lib.plan_queue_core_lib.errors import ErrorKind, PlanQueueError
from plan_queue_core_lib.plan_queue_core_lib.graph import MAX_BRIEF_CHARS, MAX_STEPS, StepDraft
from plan_queue_core_lib.plan_queue_core_lib.proposer_prompt import (
    SPLIT_CLOSE,
    SPLIT_OPEN,
    build_files_prompt,
)
from plan_queue_core_lib.plan_queue_core_lib.service import PlanQueueService
from plan_queue_core_lib.plan_queue_core_lib.split import SplitProposalError, parse_task_split
from plan_queue_core_lib.plan_queue_core_lib.store import PlanQueueStore
from plan_queue_core_lib.plan_queue_core_lib.tests.fakes import (
    FakeLauncher,
    FakeProbe,
    FakeProgress,
    FakeProposer,
    FakeWording,
    ManualClock,
    wait_until,
)

REPOS = ['api', 'web']
FILES = {
    'tasks/01-realm.md': '# 01: Realm\n\nMake the realm.',
    'tasks/02-login.md': '# 02: Login\n\n**Depends on:** 01.',
}


def mapped(*steps, notes: str = '') -> str:
    """A proposer reply that maps files (``file`` in place of ``brief``)."""
    return f'{SPLIT_OPEN}\n{json.dumps({"steps": list(steps), "notes": notes})}\n{SPLIT_CLOSE}'


def file_step(step_id: str, path: str, depends_on=(), repositories=()) -> dict:
    return {
        'id': step_id, 'title': f'Title {step_id}', 'file': path,
        'depends_on': list(depends_on), 'repositories': list(repositories),
    }


GOOD = mapped(
    file_step('t01', 'tasks/01-realm.md', repositories=['api']),
    file_step('t02', 'tasks/02-login.md', depends_on=['t01']),
    notes='mapped',
)


class ParseFilesTests(unittest.TestCase):
    def _parse(self, text):
        return parse_task_split(text, allowed_repositories=REPOS, files=FILES)

    def _refused(self, text, message) -> None:
        with self.assertRaises(SplitProposalError) as caught:
            self._parse(text)
        self.assertIn(message, str(caught.exception))

    def test_each_brief_is_its_file_exactly(self) -> None:
        proposal = self._parse(GOOD)
        self.assertEqual([step.brief for step in proposal.steps], [
            '# 01: Realm\n\nMake the realm.', '# 02: Login\n\n**Depends on:** 01.',
        ])
        self.assertEqual(proposal.files, ('tasks/01-realm.md', 'tasks/02-login.md'))
        self.assertEqual(proposal.steps[1].depends_on, ('t01',))
        self.assertEqual(proposal.notes, 'mapped')

    def test_a_brief_the_agent_wrote_is_ignored(self) -> None:
        step = dict(file_step('t01', 'tasks/01-realm.md'), brief='rewritten by the agent')
        proposal = self._parse(mapped(step, file_step('t02', 'tasks/02-login.md')))
        self.assertEqual(proposal.steps[0].brief, FILES['tasks/01-realm.md'])

    def test_every_file_once(self) -> None:
        self._refused(mapped(file_step('t01', 'tasks/01-realm.md')), 'no step was made from tasks/02-login.md')
        self._refused(
            mapped(file_step('t01', 'tasks/01-realm.md'), file_step('t02', 'tasks/01-realm.md')),
            'more than one step was made from tasks/01-realm.md',
        )

    def test_a_step_must_name_one_of_the_files(self) -> None:
        self._refused(mapped({'id': 't01', 'title': 'T'}), 'step 1 names no "file"')
        self._refused(mapped(dict(file_step('t01', 'x'), file='  ')), 'step 1 names no "file"')
        self._refused(mapped(file_step('t01', 'tasks/99.md')), '"tasks/99.md", which is not one of the files')
        self._refused(mapped('not an object'), 'step 1 is not an object')

    def test_an_id_and_a_title_are_still_required(self) -> None:
        step = file_step('t01', 'tasks/01-realm.md')
        del step['title']
        self._refused(mapped(step, file_step('t02', 'tasks/02-login.md')), 'step 1 has no text "title"')

    def test_the_split_rules_still_apply(self) -> None:
        self._refused(
            mapped(file_step('t01', 'tasks/01-realm.md', depends_on=['t02']),
                   file_step('t02', 'tasks/02-login.md', depends_on=['t01'])),
            'in a circle',
        )


class FilesPromptTests(unittest.TestCase):
    def test_a_first_mapping(self) -> None:
        prompt = build_files_prompt(
            plan_title='Sign-in', wrapped_files=[('tasks/01-realm.md', '<u>REALM</u>'), ('tasks/02.md', '<u>2</u>')],
            wrapped_overview='<u>OVERVIEW</u>', repositories=REPOS, guidance='Host rule.',
        )
        self.assertIn('Do NOT split, merge, reorder or rewrite them', prompt)
        self.assertIn('<u>OVERVIEW</u>', prompt)
        self.assertLess(prompt.index('File tasks/01-realm.md:'), prompt.index('File tasks/02.md:'))
        self.assertIn('No "brief"', prompt)
        self.assertIn('Host rule.', prompt)
        self.assertIn('"file": "<folder>/01-realm.md"', prompt)
        self.assertTrue(prompt.rstrip().endswith(SPLIT_CLOSE))
        self.assertNotIn('Your previous split', prompt)

    def test_no_overview_and_a_revision(self) -> None:
        draft = StepDraft('t01', 'Realm', 'the whole long file text', depends_on=(), repositories=('api',))
        prompt = build_files_prompt(
            plan_title='Sign-in', wrapped_files=[('tasks/01-realm.md', '<u>REALM</u>')], wrapped_overview='',
            repositories=REPOS, previous_steps=[(draft, 'tasks/01-realm.md')],
            feedback='t02 waits for t01', problem='step 2 names no "file"',
        )
        self.assertNotIn('overview of the split', prompt)
        previous = prompt[prompt.index('Your previous split was:'):]
        self.assertIn('"file": "tasks/01-realm.md"', previous)
        self.assertNotIn('the whole long file text', previous)  # the file stands for it
        self.assertIn('t02 waits for t01', prompt)
        self.assertIn('step 2 names no "file"', prompt)


class FilesServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix='plan-split-files-')
        self.addCleanup(tmp.cleanup)
        self.proposer = FakeProposer(GOOD)
        self.service = PlanQueueService(
            store=PlanQueueStore(tmp.name), proposer=self.proposer, wording=FakeWording(),
            progress=FakeProgress(), probe=FakeProbe(), clock=ManualClock(5_000.0), tick_seconds=0.01,
        )
        self.service.bind_launcher(FakeLauncher())
        self.addCleanup(self.service.shutdown)

    def _files(self, files=FILES):
        return [SplitFile(path, text) for path, text in files.items()]

    def _proposed(self, overview: str = '# Overview'):
        plan = self.service.propose('SRC-1', plan_markdown=overview, repository_ids=REPOS,
                                    title='Sign-in', split_files=self._files())
        self.assertTrue(wait_until(lambda: self.service.get(plan.plan_id).status != PlanStatus.PROPOSING))
        return self.service.get(plan.plan_id)

    def test_files_become_steps_with_their_text_and_source(self) -> None:
        plan = self._proposed()
        self.assertEqual(plan.status, PlanStatus.PROPOSED)
        self.assertEqual([(step.id, step.source_file) for step in plan.steps],
                         [('t01', 'tasks/01-realm.md'), ('t02', 'tasks/02-login.md')])
        self.assertEqual(plan.steps[0].brief, FILES['tasks/01-realm.md'])
        prompt = self.proposer.prompts[0]
        self.assertIn('<untrusted source="tasks/01-realm.md">', prompt)
        self.assertIn('<untrusted source="overview of SRC-1">', prompt)

    def test_no_overview_is_fine(self) -> None:
        plan = self._proposed(overview='')
        self.assertEqual(plan.status, PlanStatus.PROPOSED)
        self.assertNotIn('overview of SRC-1', self.proposer.prompts[0])

    def test_a_revision_maps_the_same_files_again(self) -> None:
        plan = self._proposed()
        self.service.revise(plan.plan_id, 'keep it')
        self.assertTrue(wait_until(lambda: len(self.proposer.prompts) == 2
                                   and self.service.get(plan.plan_id).status == PlanStatus.PROPOSED))
        self.assertIn('"file": "tasks/02-login.md"', self.proposer.prompts[1])
        self.assertIn('keep it', self.proposer.prompts[1])

    def test_an_edit_keeps_each_steps_source_file(self) -> None:
        plan = self._proposed()
        drafts = [StepDraft('t01', 'Realm!', 'edited', repositories=('api',)), StepDraft('new', 'New', 'b')]
        edited = self.service.edit(plan.plan_id, drafts)
        self.assertEqual([step.source_file for step in edited.steps], ['tasks/01-realm.md', ''])

    def test_a_split_that_cannot_become_steps_is_refused_up_front(self) -> None:
        too_many = {f'tasks/{n:02d}.md': 'x' for n in range(MAX_STEPS + 1)}
        cases = [
            (too_many, f'at most {MAX_STEPS} tasks'),
            ({'tasks/a.md': '  '}, 'tasks/a.md is empty'),
            ({'tasks/a.md': 'x' * (MAX_BRIEF_CHARS + 1)}, f'at most {MAX_BRIEF_CHARS}'),
            ({' ': 'x'}, 'twice or not at all'),
        ]
        for files, message in cases:
            with self.subTest(message), self.assertRaises(PlanQueueError) as caught:
                self.service.propose('SRC-1', plan_markdown='', repository_ids=REPOS, split_files=self._files(files))
            self.assertEqual(caught.exception.kind, ErrorKind.INVALID)
            self.assertIn(message, str(caught.exception))
        duplicate = [SplitFile('tasks/a.md', 'x'), SplitFile('tasks/a.md', 'y')]
        with self.assertRaises(PlanQueueError):
            self.service.propose('SRC-1', plan_markdown='', repository_ids=REPOS, split_files=duplicate)

    def test_no_plan_and_no_files_is_still_refused(self) -> None:
        with self.assertRaises(PlanQueueError):
            self.service.propose('SRC-1', plan_markdown=' ', repository_ids=REPOS)


class FilesDataTests(unittest.TestCase):
    def test_split_files_and_source_files_survive_a_save(self) -> None:
        plan = Plan.new('SRC-1', plan_markdown='', repository_ids=REPOS, now=1.0,
                        split_files=[SplitFile('tasks/01.md', 'one')])
        plan.steps = [Step(id='t01', title='T', brief='one', source_file='tasks/01.md')]
        loaded = Plan.from_dict(json.loads(json.dumps(plan.to_dict())))
        self.assertEqual(loaded.split_files, [SplitFile('tasks/01.md', 'one')])
        self.assertEqual(loaded.steps[0].source_file, 'tasks/01.md')

    def test_an_older_plan_has_no_split_files(self) -> None:
        loaded = Plan.from_dict({'plan_id': 'p', 'source_task_id': 'S', 'split_files': 'junk',
                                 'steps': [{'id': 'a'}]})
        self.assertEqual(loaded.split_files, [])
        self.assertEqual(loaded.steps[0].source_file, '')


if __name__ == '__main__':
    unittest.main()
