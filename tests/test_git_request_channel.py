"""Agent → kato git requests: the agent ASKS, kato performs.

Blocking the git kato owns is correct, but on its own it leaves the agent
stuck — it hits the wall and reports "git is forbidden", which reads to an
operator as kato being broken. This is the other half of that: a channel
for the agent to say what it needs and why.

The load-bearing rule: asking is not deciding. Anything that publishes or
moves the branch still needs the operator, and push/PR are not reachable
through the channel at all — kato publishes on the Done button and on
nothing else.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock

from kato_core_lib.data_layers.service.git_request_service import GitRequestService
from kato_core_lib.helpers.git_request import (
    GIT_REQUEST_FILENAME,
    GIT_RESULT_FILENAME,
    GitRequestError,
    agent_guidance_text,
    clear_request,
    parse_request,
    read_request,
    write_result,
)


class ParseTests(unittest.TestCase):
    def test_a_well_formed_request_parses(self) -> None:
        request = parse_request({
            'operation': 'commit', 'repository_id': 'admin',
            'reason': 'checkpoint before refactor', 'message': 'wip',
        })
        self.assertEqual(request.operation, 'commit')
        self.assertEqual(request.repository_id, 'admin')
        self.assertTrue(request.needs_approval)

    def test_a_reason_is_required(self) -> None:
        # The operator sees the reason and nothing else when deciding, so a
        # request without one is unanswerable rather than merely untidy.
        with self.assertRaises(GitRequestError) as caught:
            parse_request({'operation': 'commit'})
        self.assertIn('reason', str(caught.exception))

    def test_hyphens_and_case_are_accepted(self) -> None:
        self.assertEqual(
            parse_request({'operation': 'Create-Branch', 'reason': 'x'}).operation,
            'create_branch',
        )

    def test_an_unknown_operation_lists_what_is_supported(self) -> None:
        with self.assertRaises(GitRequestError) as caught:
            parse_request({'operation': 'rebase', 'reason': 'x'})
        self.assertIn('commit', str(caught.exception))

    def test_a_non_object_payload_is_rejected(self) -> None:
        with self.assertRaises(GitRequestError):
            parse_request(['commit'])


class PublishIsNotReachableThroughTheChannelTests(unittest.TestCase):
    """kato publishes on an operator action and on nothing else.

    Routing a push through a request file would be the same rule broken by
    a different door, so these are refused at PARSE time — they never reach
    an executor at all.
    """

    def test_push_is_refused_and_points_at_the_done_button(self) -> None:
        with self.assertRaises(GitRequestError) as caught:
            parse_request({'operation': 'push', 'reason': 'ship it'})
        self.assertIn('Done button', str(caught.exception))

    def test_opening_a_pull_request_is_refused(self) -> None:
        with self.assertRaises(GitRequestError) as caught:
            parse_request({'operation': 'open_pull_request', 'reason': 'ready'})
        self.assertIn('Done button', str(caught.exception))

    def test_config_is_refused_as_an_execution_surface(self) -> None:
        with self.assertRaises(GitRequestError) as caught:
            parse_request({'operation': 'config', 'reason': 'set user.name'})
        self.assertIn('execution', str(caught.exception).lower())

    def test_reset_is_refused_and_names_the_alternative(self) -> None:
        with self.assertRaises(GitRequestError) as caught:
            parse_request({'operation': 'reset', 'reason': 'undo'})
        self.assertIn('git restore --source', str(caught.exception))

    def test_no_executor_exists_for_a_refused_operation(self) -> None:
        # Belt and braces: even if parse_request were bypassed, there is no
        # handler to run.
        for operation in ('push', 'open_pull_request', 'config', 'reset'):
            with self.subTest(operation=operation):
                self.assertFalse(hasattr(GitRequestService, f'_do_{operation}'))


class ApprovalClassificationTests(unittest.TestCase):
    def test_branch_and_publish_class_operations_need_the_operator(self) -> None:
        for operation in ('commit', 'create_branch', 'switch_branch', 'clean'):
            with self.subTest(operation=operation):
                self.assertTrue(
                    parse_request({'operation': operation, 'reason': 'x'}).needs_approval,
                )

    def test_staging_does_not_prompt(self) -> None:
        # kato stages everything at publish anyway; prompting for it would
        # be noise, and noise is what teaches an operator to click through.
        for operation in ('stage', 'unstage'):
            with self.subTest(operation=operation):
                self.assertFalse(
                    parse_request({'operation': operation, 'reason': 'x'}).needs_approval,
                )


class FileChannelTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.workspace = self._dir.name

    def _write(self, payload) -> None:
        with open(os.path.join(self.workspace, GIT_REQUEST_FILENAME), 'w') as handle:
            handle.write(payload if isinstance(payload, str) else json.dumps(payload))

    def test_no_file_is_not_an_error(self) -> None:
        self.assertIsNone(read_request(self.workspace))

    def test_a_request_round_trips(self) -> None:
        self._write({'operation': 'commit', 'reason': 'why', 'repository_id': 'r'})
        self.assertEqual(read_request(self.workspace).operation, 'commit')

    def test_malformed_json_answers_the_agent_instead_of_crashing(self) -> None:
        self._write('{not json')
        with self.assertRaises(GitRequestError) as caught:
            read_request(self.workspace)
        self.assertIn('JSON', str(caught.exception))

    def test_the_request_is_cleared_once_handled(self) -> None:
        # A leftover file would be re-read forever.
        self._write({'operation': 'stage', 'reason': 'x', 'paths': ['a.js']})
        clear_request(self.workspace)
        self.assertIsNone(read_request(self.workspace))

    def test_clearing_a_missing_file_is_harmless(self) -> None:
        clear_request(self.workspace)   # must not raise

    def test_the_outcome_is_always_written_back(self) -> None:
        # An agent that asked and heard nothing cannot tell "not handled
        # yet" from "refused", and will hang or retry forever.
        write_result(self.workspace, ok=False, detail='refused', operation='push')
        with open(os.path.join(self.workspace, GIT_RESULT_FILENAME)) as handle:
            payload = json.load(handle)
        self.assertFalse(payload['ok'])
        self.assertEqual(payload['detail'], 'refused')


class ExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.repo = os.path.join(self._dir.name, 'admin')
        os.makedirs(self.repo)
        self.repository_service = mock.MagicMock()
        self.repository_service._working_tree_status.return_value = ' M a.js'
        workspace_manager = mock.MagicMock()
        workspace_manager.repository_path.return_value = self.repo
        self.service = GitRequestService(
            self.repository_service, workspace_manager, logger=mock.MagicMock(),
        )

    def _run(self, payload):
        return self.service.execute('T-1', parse_request(payload))

    def _argv(self):
        return self.repository_service._run_git.call_args.args[1]

    def test_commit_stages_then_commits_with_the_message(self) -> None:
        summary = self._run({
            'operation': 'commit', 'repository_id': 'admin',
            'reason': 'checkpoint', 'message': 'add parser',
        })
        self.assertIn('add parser', summary)
        self.assertEqual(self._argv()[:2], ['commit', '-m'])

    def test_commit_on_a_clean_tree_says_so_rather_than_claiming_success(self) -> None:
        self.repository_service._working_tree_status.return_value = ''
        summary = self._run({
            'operation': 'commit', 'repository_id': 'admin', 'reason': 'x',
        })
        self.assertIn('nothing to commit', summary)
        self.repository_service._run_git.assert_not_called()

    def test_stage_is_path_scoped(self) -> None:
        self._run({
            'operation': 'stage', 'repository_id': 'admin',
            'reason': 'x', 'paths': ['src/a.js'],
        })
        argv = self._argv()
        self.assertIn('--', argv)
        self.assertEqual(argv[argv.index('--') + 1:], ['src/a.js'])

    def test_clean_refuses_a_whole_tree_pathspec(self) -> None:
        # ``git clean -fd`` with no pathspec deletes every untracked file in
        # the clone, including work the agent has not had committed yet.
        for path in ('.', '..', '*', '/etc'):
            with self.subTest(path=path):
                with self.assertRaises(GitRequestError):
                    self._run({
                        'operation': 'clean', 'repository_id': 'admin',
                        'reason': 'x', 'paths': [path],
                    })

    def test_clean_with_no_paths_is_refused(self) -> None:
        with self.assertRaises(GitRequestError):
            self._run({'operation': 'clean', 'repository_id': 'admin', 'reason': 'x'})

    def test_a_branch_operation_needs_a_branch_name(self) -> None:
        with self.assertRaises(GitRequestError) as caught:
            self._run({
                'operation': 'switch_branch', 'repository_id': 'admin', 'reason': 'x',
            })
        self.assertIn('branch', str(caught.exception))

    def test_an_unknown_repository_tells_the_agent_what_to_check(self) -> None:
        self.service._workspace_manager.repository_path.return_value = '/nope'
        with self.assertRaises(GitRequestError) as caught:
            self._run({
                'operation': 'commit', 'repository_id': 'ghost', 'reason': 'x',
            })
        self.assertIn('ghost', str(caught.exception))

    def test_a_missing_repository_id_is_named_as_the_problem(self) -> None:
        with self.assertRaises(GitRequestError) as caught:
            self._run({'operation': 'commit', 'reason': 'x'})
        self.assertIn('repository_id', str(caught.exception))

    def test_every_operation_goes_through_katos_hardened_git_client(self) -> None:
        # Never a bare subprocess: the client is what disables hooks, pins
        # the remote, and validates argv.
        self._run({
            'operation': 'commit', 'repository_id': 'admin', 'reason': 'x',
        })
        self.assertTrue(self.repository_service._run_git.called)


class GuidanceTests(unittest.TestCase):
    def test_it_tells_the_agent_not_to_report_itself_blocked(self) -> None:
        text = agent_guidance_text()
        self.assertIn('do NOT report yourself as blocked', text)

    def test_it_names_the_files_and_the_supported_operations(self) -> None:
        text = agent_guidance_text()
        self.assertIn(GIT_REQUEST_FILENAME, text)
        self.assertIn(GIT_RESULT_FILENAME, text)
        self.assertIn('commit', text)

    def test_it_says_publishing_is_not_available_here(self) -> None:
        self.assertIn('Done button', agent_guidance_text())


if __name__ == '__main__':
    unittest.main()
