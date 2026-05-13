"""Flow #5 — Review comment is a fix request (default path).

A-Z scenario:

    1. PR is in "In Review" state with kato as author.
    2. Reviewer posts an imperative comment ("Add a null check.").
    3. Scan picks it up; ``is_question_only_batch`` returns False
       → route to FIX mode.
    4. Any existing live session for this task is terminated first
       (one-session-per-task invariant).
    5. Agent spawned with a fix-mode prompt + the PR branch.
    6. Agent edits files in the workspace clone.
    7. kato runs ``_review_fix_produced_changes`` check.
    8. If changes: commit + push + reply with the auto-update header +
       resolve thread.
    9. If no changes: reply with the "_did nothing — here is why_" body
       (not the fix header) and leave the thread OPEN.

Adversarial regression modes this pins:
    - Agent fix-mode WITHOUT a prior terminate would mean two
      subprocess for one task — concurrent writes to the same workspace.
    - ``review_comment_reply_body`` building an answer-style reply for
      fix-mode would lose the "Kato addressed and pushed" header.
    - An empty fix prompt could be silently submitted.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from kato_core_lib.helpers.review_comment_utils import (
    ReviewReplyTemplate,
    review_comment_reply_body,
)


def _comment(body, comment_id='c1', pull_request_id='pr1'):
    # Mirrors ReviewComment fields used by the prompt builders.
    return SimpleNamespace(
        body=body,
        comment_id=comment_id,
        pull_request_id=pull_request_id,
        author='reviewer',
        file_path='auth.py',
        line_number=10,
        line_type='ADDED',
        commit_sha='abc1234',
    )


# ---------------------------------------------------------------------------
# fix_review_comments: pre-spawn validation + session teardown.
# ---------------------------------------------------------------------------


class FlowReviewCommentFixModeSpawnTests(unittest.TestCase):

    def _make_runner(self, session_manager):
        from kato_core_lib.data_layers.service.planning_session_runner import (
            PlanningSessionRunner, StreamingSessionDefaults,
        )
        return PlanningSessionRunner(
            session_manager=session_manager,
            defaults=StreamingSessionDefaults(),
        )

    def test_flow_fix_mode_empty_comment_list_rejected(self) -> None:
        # Defensive: no comments = nothing to fix. The runner must
        # refuse rather than spawn an empty-prompt session.
        manager = MagicMock()
        runner = self._make_runner(manager)
        with self.assertRaises(ValueError):
            runner.fix_review_comments(
                [], branch_name='feature/T1', task_id='T1',
            )

    def test_flow_fix_mode_empty_task_id_rejected(self) -> None:
        manager = MagicMock()
        runner = self._make_runner(manager)
        with self.assertRaises(ValueError):
            runner.fix_review_comments(
                [_comment('Fix the null check.')],
                branch_name='feature/T1', task_id='',
            )

    def test_flow_fix_mode_whitespace_task_id_rejected(self) -> None:
        manager = MagicMock()
        runner = self._make_runner(manager)
        with self.assertRaises(ValueError):
            runner.fix_review_comments(
                [_comment('Fix the null check.')],
                branch_name='feature/T1', task_id='   ',
            )

    def test_flow_fix_mode_terminates_existing_session_before_spawn(self) -> None:
        # The one-session-per-task invariant: if a session is already
        # alive (e.g., the operator's chat session), it must be
        # terminated BEFORE the fix-mode spawn. Otherwise two
        # subprocesses write to the same workspace concurrently and
        # git state will tangle.
        manager = MagicMock()
        # Existing session exists.
        manager.get_session.return_value = SimpleNamespace(is_alive=True)
        # Stub _run_to_terminal underbelly: prevent the actual run.
        runner = self._make_runner(manager)
        runner._run_to_terminal = MagicMock(return_value={
            'success': True, 'message': 'done',
        })

        runner.fix_review_comments(
            [_comment('Add a null check.')],
            branch_name='feature/T1',
            task_id='T1',
        )

        manager.terminate_session.assert_called_once_with('T1')

    def test_flow_fix_mode_no_existing_session_no_terminate(self) -> None:
        # Negative case: no session exists → no terminate call. A
        # spurious terminate when nothing is running would silently
        # error on some backends.
        manager = MagicMock()
        manager.get_session.return_value = None
        runner = self._make_runner(manager)
        runner._run_to_terminal = MagicMock(return_value={'success': True})

        runner.fix_review_comments(
            [_comment('Add a null check.')],
            branch_name='feature/T1',
            task_id='T1',
        )

        manager.terminate_session.assert_not_called()

    def test_flow_fix_mode_single_comment_uses_singular_prompt_builder(self) -> None:
        # Performance + clarity: a single-comment batch should use the
        # simpler singular prompt, not the batch builder which adds
        # batching ceremony.
        from claude_core_lib.claude_core_lib.cli_client import ClaudeCliClient

        manager = MagicMock()
        manager.get_session.return_value = None
        runner = self._make_runner(manager)

        captured_prompts = []
        def fake_terminal(**kwargs):
            captured_prompts.append(kwargs.get('initial_prompt'))
            return {'success': True}

        runner._run_to_terminal = fake_terminal
        runner.fix_review_comments(
            [_comment('Add a null check.')],
            branch_name='feature/T1',
            task_id='T1',
            repository_local_path='/tmp/T1/repo',
        )

        # Compare against the canonical singular builder.
        expected = ClaudeCliClient._build_review_prompt(
            _comment('Add a null check.'),
            'feature/T1',
            workspace_path='/tmp/T1/repo',
            mode='fix',
        )
        self.assertEqual(captured_prompts[0], expected)

    def test_flow_fix_mode_batch_comments_use_batch_prompt_builder(self) -> None:
        # Two-or-more comments → batch builder. The batch builder
        # includes batching context that the singular builder doesn't.
        from claude_core_lib.claude_core_lib.cli_client import ClaudeCliClient

        manager = MagicMock()
        manager.get_session.return_value = None
        runner = self._make_runner(manager)

        captured = []
        runner._run_to_terminal = lambda **kw: captured.append(kw.get('initial_prompt')) or {'success': True}

        comments = [
            _comment('Add a null check.', comment_id='c1'),
            _comment('Rename foo to bar.', comment_id='c2'),
        ]
        runner.fix_review_comments(
            comments,
            branch_name='feature/T1',
            task_id='T1',
            repository_local_path='/tmp/T1/repo',
        )

        expected = ClaudeCliClient._build_review_comments_batch_prompt(
            comments, 'feature/T1', workspace_path='/tmp/T1/repo', mode='fix',
        )
        self.assertEqual(captured[0], expected)

    def test_flow_fix_mode_passes_branch_name_to_terminal(self) -> None:
        # The branch_name propagation matters: the fix MUST land on
        # the PR branch, not on master/main. A regression that
        # dropped the branch_name would push the fix to the default
        # branch.
        manager = MagicMock()
        manager.get_session.return_value = None
        runner = self._make_runner(manager)

        captured = {}
        runner._run_to_terminal = lambda **kw: captured.update(kw) or {'success': True}

        runner.fix_review_comments(
            [_comment('Add a null check.')],
            branch_name='feature/T1-with-special-chars',
            task_id='T1',
        )
        self.assertEqual(captured['branch_name'], 'feature/T1-with-special-chars')


# ---------------------------------------------------------------------------
# Reply-body construction in fix-mode.
# ---------------------------------------------------------------------------


class FlowReviewCommentFixReplyBodyTests(unittest.TestCase):

    def test_flow_fix_reply_body_uses_addressed_header(self) -> None:
        # The "Kato addressed and pushed an update" header — distinct
        # from the answer-mode "no code changed" header.
        body = review_comment_reply_body({
            'success': True, 'message': 'Added null check in auth.py.',
        })
        self.assertIn('Kato addressed', body)
        self.assertNotIn('No code was changed', body)

    def test_flow_fix_reply_body_includes_agent_summary(self) -> None:
        # Reviewer needs to see WHAT changed, not just that kato
        # claimed success.
        body = review_comment_reply_body({
            'success': True,
            'message': 'Added null check in auth.py at line 42.',
        })
        self.assertIn('auth.py', body)

    def test_flow_fix_reply_body_with_no_summary_uses_did_nothing_fallback(self) -> None:
        # Worst case: kato called this a "fix" but the agent produced
        # nothing useful. The reply body must STILL render visibly —
        # not a blank reply. Reviewer needs to know nothing happened.
        body = review_comment_reply_body({'success': True})
        self.assertGreater(
            len(body), len(ReviewReplyTemplate.HEADER) + 10,
            'fix-mode reply body with no summary collapsed to just the '
            'header — reviewer cannot tell anything happened',
        )

    def test_flow_fix_reply_body_with_separator_between_header_and_body(self) -> None:
        # Same separator contract as answer-mode: a visual rule
        # between the boilerplate header and the per-comment summary.
        body = review_comment_reply_body({
            'success': True, 'message': 'Added null check.',
        })
        self.assertIn(ReviewReplyTemplate.SEPARATOR.strip(), body)


if __name__ == '__main__':
    unittest.main()
