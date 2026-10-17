"""A split proposed in the task's chat stays a conversation: the operator tells
the agent what to change there, and a reply carrying a new split replaces the
proposal — no need to go back to the queue's own buttons."""

from __future__ import annotations

import tempfile
import unittest

from plan_queue_core_lib.plan_queue_core_lib.data.plan import PlanStatus, SplitFile
from plan_queue_core_lib.plan_queue_core_lib.service import PlanQueueService
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
from plan_queue_core_lib.plan_queue_core_lib.tests.test_split_files import GOOD as FILES_GOOD
from plan_queue_core_lib.plan_queue_core_lib.tests.test_split_files import FILES, file_step, mapped

FIRST = split_reply([step_spec('api'), step_spec('ui', depends_on=['api'])])
STEERED = split_reply([step_spec('api'), step_spec('ui'), step_spec('docs', depends_on=['ui'])], notes='as asked')


class FakeConversation(object):
    """The task's chat: replies the agent finished, in order."""

    def __init__(self) -> None:
        self.replies: list[tuple[float, str]] = []
        self.error: Exception | None = None
        self.during = None   # called while the queue reads (a change racing the read)

    def say(self, at: float, text: str) -> None:
        self.replies.append((at, text))

    def latest_reply(self, source_task_id, since):
        if self.error is not None:
            raise self.error
        if self.during is not None:
            self.during()
        newer = [reply for reply in self.replies if reply[0] > since]
        return newer[-1] if newer else None


class SplitConversationTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix='plan-chat-')
        self.addCleanup(tmp.cleanup)
        self.clock = ManualClock(5_000.0)
        self.chat = FakeConversation()
        self.events: list[str] = []
        self.proposer = FakeProposer(FIRST)
        self.service = PlanQueueService(
            store=PlanQueueStore(tmp.name), proposer=self.proposer, wording=FakeWording(),
            progress=FakeProgress(), probe=FakeProbe(), clock=self.clock, tick_seconds=60,
            observer=lambda plan, event: self.events.append(event), conversation=self.chat,
        )
        self.service.bind_launcher(FakeLauncher())
        self.addCleanup(self.service.shutdown)

    def _proposed(self, **kwargs):
        plan = self.service.propose('SRC-1', plan_markdown=kwargs.pop('plan_markdown', '# plan'),
                                    repository_ids=['api', 'web'], **kwargs)
        self.assertTrue(wait_until(lambda: self.service.get(plan.plan_id).status != PlanStatus.PROPOSING))
        return self.service.get(plan.plan_id)

    def test_the_proposals_own_reply_is_not_read_again(self) -> None:
        self.chat.say(4_999.0, STEERED)  # the turn that proposed it, before it was recorded
        plan = self._proposed()
        self.assertEqual(plan.chat_seen_at, 5_000.0)
        self.service.tick()
        self.assertEqual([step.id for step in self.service.get(plan.plan_id).steps], ['api', 'ui'])

    def test_a_reply_with_a_new_split_replaces_the_proposal(self) -> None:
        plan = self._proposed()
        self.chat.say(5_010.0, 'Sure — merged the two UI tasks:\n' + STEERED)
        self.service.tick()
        steered = self.service.get(plan.plan_id)
        self.assertEqual([step.id for step in steered.steps], ['api', 'ui', 'docs'])
        self.assertEqual((steered.notes, steered.revision, steered.chat_seen_at, steered.status),
                         ('as asked', plan.revision + 1, 5_010.0, PlanStatus.PROPOSED))
        self.assertIn('the split was changed in the chat', self.events)
        self.service.tick()  # read once
        self.assertEqual(self.service.get(plan.plan_id).revision, plan.revision + 1)

    def test_ordinary_chat_changes_nothing(self) -> None:
        plan = self._proposed()
        self.chat.say(5_010.0, 'We already have a discount table, by the way.')
        self.service.tick()
        after = self.service.get(plan.plan_id)
        self.assertEqual((after.revision, after.reason, after.chat_seen_at), (plan.revision, '', 5_010.0))

    def test_a_split_that_breaks_a_rule_is_said_and_the_proposal_kept(self) -> None:
        plan = self._proposed()
        self.chat.say(5_010.0, split_reply([step_spec('ui', depends_on=['nope'])]))
        self.service.tick()
        after = self.service.get(plan.plan_id)
        self.assertEqual([step.id for step in after.steps], ['api', 'ui'])
        self.assertIn('the newest split in the chat could not be used', after.reason)

    def test_a_failed_proposal_is_rescued_in_the_chat(self) -> None:
        self.proposer.replies = ['no block here']
        plan = self._proposed()
        self.assertEqual(plan.status, PlanStatus.PROPOSE_FAILED)
        self.chat.say(self.clock.now + 1, STEERED)
        self.service.tick()
        self.assertEqual(self.service.get(plan.plan_id).status, PlanStatus.PROPOSED)

    def test_a_split_written_as_files_is_steered_the_same_way(self) -> None:
        self.proposer.replies = [FILES_GOOD]
        plan = self._proposed(plan_markdown='', split_files=[SplitFile(path, text) for path, text in FILES.items()])
        self.chat.say(5_010.0, mapped(file_step('t01', 'tasks/01-realm.md'), file_step('t02', 'tasks/02-login.md')))
        self.service.tick()
        steered = self.service.get(plan.plan_id)
        self.assertEqual(steered.steps[1].depends_on, [])  # the operator said 02 does not wait for 01
        self.assertEqual(steered.steps[1].brief, FILES['tasks/02-login.md'])

    def test_an_approved_plan_no_longer_listens(self) -> None:
        plan = self._proposed()
        self.service.approve(plan.plan_id)
        self.chat.say(5_010.0, STEERED)
        self.service.tick()
        self.assertEqual([step.id for step in self.service.get(plan.plan_id).steps], ['api', 'ui'])

    def test_a_change_racing_the_read_wins(self) -> None:
        plan = self._proposed()
        self.chat.say(5_010.0, STEERED)
        self.chat.during = lambda: self.service.approve(plan.plan_id)
        self.service.tick()
        self.assertEqual(self.service.get(plan.plan_id).status, PlanStatus.APPROVED)
        self.assertEqual(len(self.service.get(plan.plan_id).steps), 2)

    def test_a_chat_that_cannot_be_read_is_skipped(self) -> None:
        self._proposed()
        self.chat.error = RuntimeError('no session')
        with self.assertLogs('PlanQueueService', 'WARNING') as logs:
            self.service.tick()
        self.assertIn('could not read the chat of SRC-1: no session', '\n'.join(logs.output))


if __name__ == '__main__':
    unittest.main()
