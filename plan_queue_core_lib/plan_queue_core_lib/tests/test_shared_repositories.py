"""Where a plan's steps work.

``proposer_picks_repositories=False``: the split's agent is not asked, so every
step starts in all of the plan's repositories; the operator narrows or widens a
step in an edit, and that is kept. A repository the plan's source task gains
reaches every step that has not started — a started one keeps what its agent
was given — and a waiting task that already exists is rewritten to match.
"""

from __future__ import annotations

import json
import tempfile
import unittest

from plan_queue_core_lib.plan_queue_core_lib.data.plan import PlanMode, PlanStatus
from plan_queue_core_lib.plan_queue_core_lib.graph import StepDraft, validate_split
from plan_queue_core_lib.plan_queue_core_lib.proposer_prompt import (
    SPLIT_CLOSE,
    SPLIT_OPEN,
    build_files_prompt,
    build_propose_prompt,
)
from plan_queue_core_lib.plan_queue_core_lib.service import PlanQueueService
from plan_queue_core_lib.plan_queue_core_lib.split import parse_task_split
from plan_queue_core_lib.plan_queue_core_lib.store import PlanQueueStore
from plan_queue_core_lib.plan_queue_core_lib.tests.fakes import (
    FakeLauncher,
    FakeProbe,
    FakeProgress,
    FakeProposer,
    FakeWording,
    ManualClock,
    split_reply,
    step_spec,
    wait_until,
)
from plan_queue_core_lib.plan_queue_core_lib.tests.test_split_conversation import FakeConversation

NARROW = split_reply([
    step_spec('api', repositories=['api']),
    step_spec('ui', depends_on=['api'], repositories=['web']),
])
REPOS = ['api', 'web']


def _example(prompt: str) -> dict:
    body = prompt[prompt.rindex(SPLIT_OPEN) + len(SPLIT_OPEN):prompt.rindex(SPLIT_CLOSE)]
    return json.loads(body)


class ValidationTests(unittest.TestCase):
    def test_every_step_works_in_every_repository_whatever_it_names(self) -> None:
        drafts = [
            StepDraft(id='a', title='A', brief='b', repositories=('api',)),
            StepDraft(id='b', title='B', brief='b', repositories=('not-a-repo',)),
            StepDraft(id='c', title='C', brief='b'),
        ]
        steps = validate_split(drafts, allowed_repositories=REPOS, per_step_repositories=False)
        self.assertEqual([step.repositories for step in steps], [tuple(REPOS)] * 3)
        # Per-step (the default) still checks and keeps what each names.
        with self.assertRaisesRegex(Exception, 'not one of'):
            validate_split(drafts, allowed_repositories=REPOS)

    def test_a_reply_is_read_the_same_way(self) -> None:
        proposal = parse_task_split(NARROW, allowed_repositories=REPOS, per_step_repositories=False)
        self.assertEqual([step.repositories for step in proposal.steps], [tuple(REPOS)] * 2)
        per_step = parse_task_split(NARROW, allowed_repositories=REPOS)
        self.assertEqual([step.repositories for step in per_step.steps], [('api',), ('web',)])


class PromptTests(unittest.TestCase):
    PREVIOUS = [StepDraft(id='api', title='A', brief='b', repositories=('api',))]

    def test_a_split_is_not_asked_where_each_task_works(self) -> None:
        prompt = build_propose_prompt(
            plan_title='Discounts', wrapped_plan='PLAN', repositories=REPOS,
            previous_steps=self.PREVIOUS, feedback='fewer', per_step_repositories=False,
        )
        self.assertIn('Every task works in all of these repositories: api, web.', prompt)
        self.assertNotIn('"repositories" lists', prompt)
        self.assertNotIn('"repositories"', prompt.split('Your previous split was:')[1].split('The operator')[0])
        self.assertTrue(all('repositories' not in step for step in _example(prompt)['steps']))

    def test_a_split_written_as_files_is_not_asked_either(self) -> None:
        prompt = build_files_prompt(
            plan_title='Sign-in', wrapped_files=[('tasks/01.md', 'ONE')], wrapped_overview='',
            repositories=REPOS, previous_steps=[(self.PREVIOUS[0], 'tasks/01.md')], feedback='again',
            per_step_repositories=False,
        )
        self.assertIn('Every task works in all of these repositories: api, web.', prompt)
        self.assertNotIn('whose code it changes', prompt)
        self.assertIn('- No "brief"', prompt)
        self.assertTrue(all('repositories' not in step for step in _example(prompt)['steps']))

    def test_per_step_repositories_are_still_asked_for_by_default(self) -> None:
        prompt = build_files_prompt(
            plan_title='Sign-in', wrapped_files=[('tasks/01.md', 'ONE')], wrapped_overview='',
            repositories=REPOS,
        )
        self.assertIn('Repositories a task may work in: api, web.', prompt)
        self.assertIn('whose code it changes', prompt)
        self.assertTrue(all('repositories' in step for step in _example(prompt)['steps']))


class _Service(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix='plan-split-repos-')
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name
        self.clock = ManualClock(5_000.0)
        self.events: list[str] = []
        self.chat = FakeConversation()
        self.launcher = FakeLauncher()
        self.service = self._service(proposer_picks=False)

    def _service(self, *, proposer_picks: bool) -> PlanQueueService:
        service = PlanQueueService(
            store=PlanQueueStore(self.root), proposer=FakeProposer(NARROW), wording=FakeWording(),
            progress=FakeProgress(), probe=FakeProbe(), clock=self.clock, tick_seconds=60,
            observer=lambda plan, event: self.events.append(event), conversation=self.chat,
            proposer_picks_repositories=proposer_picks,
        )
        service.bind_launcher(self.launcher)
        self.addCleanup(service.shutdown)
        return service

    def _proposed(self, service=None, source='SRC-1'):
        service = service or self.service
        plan = service.propose(source, plan_markdown='# plan', repository_ids=REPOS)
        self.assertTrue(wait_until(lambda: service.get(plan.plan_id).status == PlanStatus.PROPOSED))
        return service.get(plan.plan_id)

    @staticmethod
    def _repositories(plan) -> list[list[str]]:
        return [list(step.repositories) for step in plan.steps]

    @staticmethod
    def _drafts(plan, **repositories) -> list[StepDraft]:
        return [
            StepDraft(id=step.id, title=step.title, brief=step.brief, depends_on=tuple(step.depends_on),
                      repositories=tuple(repositories.get(step.id, step.repositories)))
            for step in plan.steps
        ]


class ServiceTests(_Service):
    def test_a_split_starts_every_step_in_all_of_them_and_the_operator_narrows_one(self) -> None:
        plan = self._proposed()
        self.assertEqual(self._repositories(plan), [REPOS, REPOS])  # the agent named narrower ones
        self.chat.say(5_010.0, NARROW)
        self.service.tick()
        steered = self.service.get(plan.plan_id)
        self.assertEqual(self._repositories(steered), [REPOS, REPOS])
        edited = self.service.edit(plan.plan_id, self._drafts(steered, ui=('web',)))
        self.assertEqual(self._repositories(edited), [REPOS, ['web']])  # kept

    def test_a_waiting_task_is_given_a_repository_in_a_live_edit(self) -> None:
        plan = self.service.approve(self._proposed().plan_id, run=True)
        self.service.tick()
        started = self.service.get(plan.plan_id)
        self.assertNotEqual(started.step('api').released_at, 0.0)
        edited = self.service.edit(
            plan.plan_id, self._drafts(started, ui=('web', 'lib')), repositories=['lib'],
        )
        self.assertEqual(edited.repository_ids, REPOS + ['lib'])
        self.assertEqual(self._repositories(edited), [REPOS, ['web', 'lib']])  # the started one as it was


class AddRepositoriesTests(_Service):
    def test_a_repository_the_source_task_gains_reaches_every_step_not_started(self) -> None:
        plan = self.service.approve(self._proposed().plan_id, run=True)
        self.service.tick()  # api starts; ui waits for it
        narrowed = self.service.edit(plan.plan_id, self._drafts(self.service.get(plan.plan_id), ui=('web',)))
        self.assertEqual(self.service.add_repositories('src-1', ['lib', '']), [plan.plan_id])
        after = self.service.get(plan.plan_id)
        self.assertEqual(after.repository_ids, REPOS + ['lib'])
        self.assertEqual(self._repositories(after), [REPOS, ['web', 'lib']])  # api started: untouched
        self.assertIn('now works in lib', self.events)
        # The waiting task already on the tracker reads the new list.
        [(request, task_id)] = self.launcher.updated[-1:]
        self.assertEqual((task_id, request.repositories), (narrowed.step('ui').task_id, ('web', 'lib')))
        # Again, another task, or nothing: no change.
        self.assertEqual(self.service.add_repositories('SRC-1', ['lib']), [])
        self.assertEqual(self.service.add_repositories('OTHER-1', ['x']), [])
        self.assertEqual(self.service.add_repositories('', ['x']), [])

    def test_a_task_that_cannot_be_rewritten_says_so(self) -> None:
        plan = self.service.approve(self._proposed().plan_id)
        task_id = self.service.get(plan.plan_id).step('api').task_id
        self.launcher.update_errors[task_id] = RuntimeError('tracker down')
        with self.assertRaisesRegex(Exception, 'tracker down'):
            self.service.add_repositories('SRC-1', ['lib'])
        self.assertEqual(self.service.get(plan.plan_id).step('api').repositories, REPOS + ['lib'])

    def test_a_shared_plan_republishes_its_record(self) -> None:
        plan = self._proposed()
        self.service._registry.mutate(plan.plan_id, lambda live: setattr(live, 'mode', PlanMode.TRACKER))
        before = self.service.get(plan.plan_id).record_version
        self.service.add_repositories('SRC-1', ['lib'])
        self.assertEqual(self.service.get(plan.plan_id).record_version, before + 1)

    def test_finished_and_followed_plans_are_left_alone(self) -> None:
        stopped = self._proposed()
        self.service.stop(stopped.plan_id)
        followed = self._proposed(source='SRC-1')
        self.service._registry.mutate(followed.plan_id, lambda live: setattr(live, 'mode', PlanMode.FOLLOWED))
        self.assertEqual(self.service.add_repositories('SRC-1', ['lib']), [])
        self.assertEqual(self.service.get(stopped.plan_id).repository_ids, REPOS)

    def test_a_host_whose_tasks_start_at_release_has_nothing_to_rewrite(self) -> None:
        self.launcher.creates_at_approval = False
        plan = self.service.approve(self._proposed().plan_id)
        self.service.add_repositories('SRC-1', ['lib'])
        self.assertEqual(self.launcher.updated, [])
        self.assertEqual(self._repositories(self.service.get(plan.plan_id)), [REPOS + ['lib']] * 2)

    def test_when_the_proposer_picks_them_a_split_keeps_what_it_named(self) -> None:
        service = self._service(proposer_picks=True)
        plan = self._proposed(service, source='SRC-9')
        self.assertEqual(self._repositories(plan), [['api'], ['web']])
        service.add_repositories('SRC-9', ['lib'])
        self.assertEqual(self._repositories(service.get(plan.plan_id)), [['api', 'lib'], ['web', 'lib']])


if __name__ == '__main__':
    unittest.main()
