"""Kato's side of the review loop, on kato's real machinery.

Everything here is real: the workspace service and its git clones, the comment
store, the parallel runner, the session manager and its streaming sessions,
the planning runner that respawns a chat, ``MainChatDelivery``, the Claude CLI
client, the loop store and the loop itself. The ONE stand-in is the ``claude``
executable (``tests/fake_claude_cli.py``): the model answers instead of
thinking, and everything around it runs as in production.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from claude_core_lib.claude_core_lib.cli_client import ClaudeCliClient
from claude_core_lib.claude_core_lib.session.manager import ClaudeSessionManager
from review_loop_core_lib.review_loop_core_lib.data.state import (
    ReviewLoopState,
    ReviewLoopStatus,
    ReviewRound,
)
from review_loop_core_lib.review_loop_core_lib.ports import Readiness, RepoDiff
from review_loop_core_lib.review_loop_core_lib.reviewer_prompt import (
    VERDICT_CLOSE,
    VERDICT_OPEN,
)
from review_loop_core_lib.review_loop_core_lib.runner import RunnerOptions
from review_loop_core_lib.review_loop_core_lib.service import ReviewLoopError
from review_loop_core_lib.review_loop_core_lib.store import ReviewLoopStore
from workspace_core_lib.workspace_core_lib import WorkspaceCoreLib

from kato_core_lib.comment_core_lib import CommentRecord, KatoCommentStatus
from kato_core_lib.data_layers.service.implementation_service import (
    ImplementationService,
)
from kato_core_lib.data_layers.service.parallel_task_runner import ParallelTaskRunner
from kato_core_lib.data_layers.service.planning_session_runner import (
    PlanningSessionRunner,
    StreamingSessionDefaults,
)
from kato_core_lib.data_layers.service.review_loop_adapters import (
    REVIEW_LOOPS_DIR_ENV,
    build_review_loop_service,
    chat_mode_refusal,
    log_review_loop_event,
    mark_interrupted_review_loops,
    review_loop_refusal,
    review_loops_root,
    shut_down_review_loops,
)
from kato_core_lib.data_layers.service.task_comment_run_service import (
    TaskCommentRunService,
)
from kato_core_lib.helpers.comment_store_utils import comment_store_for
from kato_core_lib.helpers.plan_mode_store import set_task_mode
from kato_core_lib.helpers.planning_hold_store import set_planning_hold
from kato_core_lib.helpers.review_loop_guidance import REVIEW_LOOP_FINDINGS_HEADER
from tests.fake_claude_cli import FakeClaudeCli, posix_only


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True)


def _verdict(*findings: dict) -> str:
    return f'Report.\n{VERDICT_OPEN}\n{json.dumps({"findings": list(findings)})}\n{VERDICT_CLOSE}'


def _major(symbol: str = 'run') -> dict:
    return {'severity': 'MAJOR', 'repository': 'api', 'file': 'app.py', 'line': 2,
            'symbol': symbol, 'category': 'correctness', 'title': 'wrong value',
            'detail': 'return the right value'}


def _wait(predicate, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


class _KatoWorld(object):
    """One task with two real repo clones, and kato's real services around it."""

    def __init__(self, test: unittest.TestCase, *, repos=('api', 'web')) -> None:
        tmp = tempfile.TemporaryDirectory()
        test.addCleanup(tmp.cleanup)
        self.root = Path(os.path.realpath(tmp.name))
        workspaces_root = self.root / 'workspaces'
        env = patch.dict(os.environ, {
            'KATO_WORKSPACES_ROOT': str(workspaces_root),
            'AGENT_WORKSPACES_ROOT': str(workspaces_root),
            REVIEW_LOOPS_DIR_ENV: str(self.root / 'review_loops'),
        })
        env.start()
        test.addCleanup(env.stop)
        self.cli = FakeClaudeCli(self.root / 'cli')
        self.workspaces = WorkspaceCoreLib(
            root=str(workspaces_root), max_parallel_tasks=1,
            metadata_filename='.kato-meta.json', preflight_log_filename='.kato-preflight.log',
        ).workspaces
        self.workspaces.create(
            task_id='T-1', task_summary='Add login', task_description='Email + password.',
            repository_ids=list(repos),
        )
        self.task = workspaces_root / 'T-1'
        for repo in repos:
            path = self.task / repo
            path.mkdir()
            _git(path, 'init', '-q', '-b', 'main')
            _git(path, '-c', 'user.email=t@e.st', '-c', 'user.name=T', 'commit', '-q',
                 '--allow-empty', '-m', 'base')
        self.sessions = ClaudeSessionManager(state_dir=str(self.root / 'sessions'))
        test.addCleanup(self.sessions.shutdown)
        self.parallel = ParallelTaskRunner(max_workers=1)
        test.addCleanup(self.parallel.shutdown, wait=False, cancel_futures=True)
        self.comment_runs = TaskCommentRunService(
            comment_service=None,
            session_manager=self.sessions,
            workspace_manager=self.workspaces,
            parallel_task_runner=self.parallel,
            planning_session_runner=PlanningSessionRunner(
                session_manager=self.sessions,
                defaults=StreamingSessionDefaults(binary=self.cli.binary, permission_mode='acceptEdits'),
            ),
        )
        self.implementation = ImplementationService(ClaudeCliClient(binary=self.cli.binary))
        self.logger = logging.getLogger('test.review_loop')
        self.service = build_review_loop_service(
            comment_runs=self.comment_runs,
            session_manager=self.sessions,
            workspace_manager=self.workspaces,
            implementation_service=self.implementation,
            logger=self.logger,
            options=RunnerOptions(poll_seconds=0.02, settle_seconds=0.0,
                                  session_gone_grace_seconds=5.0),
        )
        test.addCleanup(self.service.shutdown, 'test over')
        self.chat = self.service._chat

    def diffs(self, task_id: str) -> list:
        return [RepoDiff(repo_id='api', diff='diff --git a/app.py b/app.py\n+    return 2\n',
                         cwd=str(self.task / 'api'))]

    def run_loop(self) -> ReviewLoopState:
        self.service.start('T-1', diff_source=self.diffs, task_summary='Add login',
                           task_description='Email + password.')
        assert _wait(lambda: not self.service.is_running('T-1'), 30)
        return self.service.state('T-1')


class _ModeIsolation(unittest.TestCase):

    def tearDown(self) -> None:
        set_task_mode('T-1', '')
        set_planning_hold('T-1', False)


@unittest.skipUnless(posix_only(), 'the stand-in CLI is a shebang script')
class WholeLoopTests(_ModeIsolation):

    def test_findings_go_through_the_real_chat_and_the_loop_ends_clean(self) -> None:
        world = _KatoWorld(self)
        world.cli.script_replies(_verdict(_major()), _verdict())
        with self.assertLogs('test.review_loop', level='INFO') as logs:
            state = world.run_loop()

        self.assertEqual(state.status, ReviewLoopStatus.CLEAN, state.reason)
        self.assertEqual([r.outcome for r in state.rounds], ['sent', 'clean'])

        # The findings reached the REAL chat session (spawned for them, since
        # none was running), with kato's header, framing and guidance.
        messages = world.cli.chat_messages()
        self.assertEqual(len(messages), 1)
        text = messages[0]['text']
        self.assertIn(REVIEW_LOOP_FINDINGS_HEADER.format(round=1, max_rounds=5) + '\n', text)
        self.assertIn('[MAJOR] api/app.py:2 (run) — wrong value', text)
        self.assertIn('<UNTRUSTED_WORKSPACE_FILE source="task T-1 review findings, round 1">', text)
        self.assertIn('Never print <KATO_TASK_DONE>', text)
        self.assertEqual(messages[0]['cwd'], str(world.task / 'api'))

        # Each review was a fresh, read-only, throwaway run over every repo.
        reviews = world.cli.oneshot_calls()
        self.assertEqual(len(reviews), 2)
        argv = reviews[0]['argv']
        self.assertEqual(argv[argv.index('--allowedTools') + 1], 'Read,Glob,Grep')
        self.assertIn('Bash', argv[argv.index('--disallowedTools') + 1].split(','))
        self.assertIn('--no-session-persistence', argv)
        self.assertNotIn('--resume', argv)
        self.assertEqual(argv[argv.index('--add-dir') + 1], str(world.task))
        self.assertEqual(reviews[0]['cwd'], str(world.task / 'api'))
        self.assertIn('Email + password.', reviews[0]['prompt'])
        self.assertIn('+    return 2', reviews[0]['prompt'])

        lines = [record.getMessage() for record in logs.records]
        self.assertIn('Mission T-1: review loop started (up to 5 reviews)', lines)
        self.assertIn('Mission T-1: review loop round 1: findings sent to the chat', lines)
        self.assertIn(
            'Mission T-1: review loop finished (clean) after 2 round(s): '
            'round 2 found no blocking issues', lines,
        )
        # Saved under the configured loops root, outside the task's clones.
        self.assertEqual(review_loops_root(), world.root / 'review_loops')
        self.assertTrue((world.root / 'review_loops' / 'T-1' / state.loop_id).is_dir())


    def test_the_fix_turns_decisions_come_back_through_the_real_chat(self) -> None:
        # The chat's final reply (the stream-json ``result``) carries the
        # fixer's response block; a rejection with evidence settles its
        # finding, so the reviewer raising it again does not keep the loop going.
        world = _KatoWorld(self)
        evidence = 'run() returns 2 by design: see the ticket'
        world.cli.script_chat_reply(
            'Looked into it.\n<review-response>\n'
            + json.dumps({'decisions': [{'id': 'R1-1', 'decision': 'rejected', 'evidence': evidence}]})
            + '\n</review-response>'
        )
        world.cli.script_replies(_verdict(_major()), _verdict(_major()))
        state = world.run_loop()

        self.assertEqual(state.status, ReviewLoopStatus.CLEAN, state.reason)
        [response] = state.rounds[0].responses
        self.assertEqual((response.finding_id, response.decision.value, response.evidence),
                         ('R1-1', 'rejected', evidence))
        self.assertEqual(state.rounds[1].findings[0].settled_by, 'R1-1')
        self.assertIn(evidence, world.cli.oneshot_calls()[1]['prompt'])
        self.assertEqual(len(world.cli.chat_messages()), 1)
        self.assertIn('<review-response>', world.service.artifact('T-1', state.loop_id, 1, 'response'))


@unittest.skipUnless(posix_only(), 'the stand-in CLI is a shebang script')
class ChatReadinessTests(_ModeIsolation):

    def test_a_free_chat_is_ready(self) -> None:
        self.assertIs(_KatoWorld(self).chat.readiness('T-1').state, Readiness.READY)

    def test_a_plan_or_explain_chat_refuses(self) -> None:
        world = _KatoWorld(self)
        set_task_mode('T-1', 'plan')
        self.assertIs(world.chat.readiness('T-1').state, Readiness.REFUSE)
        set_task_mode('T-1', 'explain')
        self.assertIn('Explain mode', world.chat.readiness('T-1').reason)
        set_task_mode('T-1', 'bypassPermissions')
        set_planning_hold('T-1', True)  # the kato:wait-planning hold wins
        self.assertIn('Plan mode', world.chat.readiness('T-1').reason)

    def test_a_queued_diff_comment_goes_first(self) -> None:
        world = _KatoWorld(self)
        store = comment_store_for(world.workspaces, 'T-1')
        comment = store.add(CommentRecord(repo_id='api', file_path='app.py', line=1, body='rename'))
        store.update_kato_status(comment.id, kato_status=KatoCommentStatus.QUEUED.value)
        readiness = world.chat.readiness('T-1')
        self.assertIs(readiness.state, Readiness.WAIT)
        self.assertEqual(readiness.reason, 'a diff comment goes first')
        store.update_kato_status(comment.id, kato_status=KatoCommentStatus.ADDRESSED.value)
        self.assertIs(world.chat.readiness('T-1').state, Readiness.READY)

    def test_a_background_run_on_the_task_goes_first(self) -> None:
        world = _KatoWorld(self)
        release = threading.Event()
        world.parallel.submit('T-1', lambda: release.wait(10))
        self.assertEqual(world.chat.readiness('T-1').reason,
                         'kato is running this task in the background')
        release.set()
        self.assertTrue(_wait(lambda: world.chat.readiness('T-1').state is Readiness.READY))

    def test_a_chat_mid_turn_waits_and_frees_up_when_the_turn_ends(self) -> None:
        world = _KatoWorld(self)
        before = time.time()
        self.assertTrue(world.chat.deliver('T-1', 'SLOW_TURN please', force_respawn=False))
        self.assertTrue(_wait(lambda: world.chat.readiness('T-1').reason == 'the agent is mid-turn'))
        self.assertTrue(world.chat.session_alive('T-1'))
        self.assertTrue(_wait(lambda: world.chat.turn_end_since('T-1', before) is not None))
        turn = world.chat.turn_end_since('T-1', before)
        self.assertFalse(turn.is_error)
        self.assertTrue(_wait(lambda: world.chat.readiness('T-1').state is Readiness.READY))
        self.assertFalse(world.chat.is_stalled('T-1'))
        with world.chat.dispatch_lock('T-1'):
            # The SAME lock the comment runs take — not a look-alike.
            self.assertTrue(world.comment_runs.chat_delivery.dispatch_lock_for('T-1').locked())


    def test_a_fix_paused_on_an_approval_says_so_until_the_operator_answers(self) -> None:
        world = _KatoWorld(self)
        # The finding's detail reaches the chat verbatim; the stand-in CLI
        # then asks permission for a Write and waits on stdin, as claude does.
        needs_approval = dict(_major(), detail='NEEDS_APPROVAL to write notes.txt')
        world.cli.script_replies(_verdict(needs_approval), _verdict())
        world.service.start('T-1', diff_source=world.diffs, task_summary='Add login')

        waiting = 'waiting for your approval in the chat (Write)'
        self.assertTrue(_wait(lambda: world.service.state('T-1').waiting_for == waiting))
        self.assertEqual(world.chat.pending_approval('T-1'), 'Write')
        state = world.service.state('T-1')
        self.assertEqual((state.round, state.phase.value), (1, 'awaiting_fix'))
        # Waiting on the operator is not a stall: nothing is respawned.
        self.assertEqual(len(world.cli.chat_messages()), 1)

        session = world.sessions.get_session('T-1')
        [request] = session.pending_control_requests()
        session.send_permission_response(request['request_id'], True)

        self.assertTrue(_wait(lambda: not world.service.is_running('T-1'), 30))
        state = world.service.state('T-1')
        self.assertEqual(state.status, ReviewLoopStatus.CLEAN, state.reason)
        self.assertEqual(world.chat.pending_approval('T-1'), '')
        self.assertEqual(len(world.cli.chat_messages()), 1)

    def test_no_chat_means_nothing_to_approve(self) -> None:
        world = _KatoWorld(self)
        self.assertEqual(world.chat.pending_approval('T-1'), '')
        self.assertEqual(world.chat.pending_approval('NO-SUCH-TASK'), '')


@unittest.skipUnless(posix_only(), 'the stand-in CLI is a shebang script')
class RefusalTests(_ModeIsolation):

    def test_reasons_an_operator_can_act_on(self) -> None:
        world = _KatoWorld(self)
        no_reviewer = ImplementationService(object())  # e.g. OpenHands: no investigate
        self.assertIn('Claude or Codex', review_loop_refusal(
            'T-1', implementation_service=no_reviewer, workspace_manager=world.workspaces))
        world.workspaces.create(task_id='T-2', repository_ids=['never-cloned'])
        self.assertIn('no repository clone', review_loop_refusal(
            'T-2', implementation_service=world.implementation, workspace_manager=world.workspaces))
        set_task_mode('T-1', 'plan')
        self.assertIn('Plan mode', review_loop_refusal(
            'T-1', implementation_service=world.implementation, workspace_manager=world.workspaces))
        with self.assertRaisesRegex(ReviewLoopError, 'Plan mode'):
            world.service.start('T-1', diff_source=world.diffs)
        set_task_mode('T-1', '')
        self.assertEqual(chat_mode_refusal('T-1'), '')
        self.assertEqual(review_loop_refusal(
            'T-1', implementation_service=world.implementation, workspace_manager=world.workspaces), '')


class LogLineTests(unittest.TestCase):

    def test_one_mission_line_per_step(self) -> None:
        logger = logging.getLogger('test.review_loop.lines')
        state = ReviewLoopState.new('T-1', max_rounds=5, now=1.0)
        state.rounds.append(ReviewRound(number=2, started_at=1.0))
        state.status = ReviewLoopStatus.STUCK
        state.reason = 'none of the blocking issues from round 1 were fixed'
        with self.assertLogs(logger, level='INFO') as logs:
            for event in ('started', 'reviewing', 'reviewed', 'sent', 'fixed', 'finished', 'other'):
                log_review_loop_event(logger, state, event)
        self.assertEqual([record.getMessage() for record in logs.records], [
            'Mission T-1: review loop started (up to 5 reviews)',
            'Mission T-1: review loop round 2: reviewing the whole change',
            'Mission T-1: review loop round 2: 0 blocker, 0 major, 0 minor, 0 nit',
            'Mission T-1: review loop round 2: findings sent to the chat',
            'Mission T-1: review loop round 2: the chat finished its fixes',
            'Mission T-1: review loop finished (stuck) after 2 round(s): '
            'none of the blocking issues from round 1 were fixed',
        ])


@unittest.skipUnless(posix_only(), 'the stand-in CLI is a shebang script')
class BootAndShutdownTests(_ModeIsolation):

    def test_a_loop_left_running_by_the_last_process_is_interrupted_at_boot(self) -> None:
        world = _KatoWorld(self)
        orphan = ReviewLoopState.new('T-1', max_rounds=5, now=1.0)
        ReviewLoopStore(review_loops_root()).save(orphan)
        self.assertEqual(mark_interrupted_review_loops(world.service), 1)
        state = world.service.state('T-1')
        self.assertEqual(state.status, ReviewLoopStatus.INTERRUPTED)
        self.assertIn('restarted', state.reason)

    def test_shutdown_interrupts_a_running_loop(self) -> None:
        world = _KatoWorld(self)
        store = comment_store_for(world.workspaces, 'T-1')
        comment = store.add(CommentRecord(repo_id='api', file_path='app.py', line=1, body='x'))
        store.update_kato_status(comment.id, kato_status=KatoCommentStatus.QUEUED.value)
        world.service.start('T-1', diff_source=world.diffs)  # waits behind the comment
        self.assertTrue(_wait(lambda: world.service.state('T-1').waiting_for == 'a diff comment goes first'))
        shut_down_review_loops(world.service)
        state = world.service.state('T-1')
        self.assertEqual(state.status, ReviewLoopStatus.INTERRUPTED)
        self.assertIn('shut down', state.reason)


if __name__ == '__main__':
    unittest.main()
