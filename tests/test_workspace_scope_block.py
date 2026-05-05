"""Tests for the workspace-scope boundary block prepended to every agent prompt.

Pin down two surfaces:

1. ``workspace_scope_block(allowed_paths)`` renders the unmissable
   "STRICT BOUNDARY" header listing the operator's per-task workspace
   paths and explicitly forbidding access to the operator's source
   repos at REPOSITORY_ROOT_PATH and other tasks' workspaces.
2. The prompt builders for **every** agent path — implementation,
   review-fix singular, review-fix batched, and the answer-mode
   variants — prepend the block when workspace paths are available.

The block being literally first in the prompt is the user-facing
contract (the screenshot showed kato writing into source folders
even though architecture.md asked it not to). Tests verify the
prefix is at the very top.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from claude_core_lib.claude_core_lib.cli_client import ClaudeCliClient
from kato_core_lib.data_layers.data.fields import (
    PullRequestFields,
    ReviewCommentFields,
)
from kato_core_lib.data_layers.data.review_comment import ReviewComment
from kato_core_lib.data_layers.data.task import Task
from kato_core_lib.helpers.agent_prompt_utils import workspace_scope_block
from kato_core_lib.helpers.task_context_utils import PreparedTaskContext


class WorkspaceScopeBlockTests(unittest.TestCase):
    """The helper itself — content + edge cases."""

    def test_empty_path_list_returns_empty_string(self) -> None:
        # No paths → no block. Caller's prompt builder sees ``''``
        # and renders the rest of the prompt unchanged.
        self.assertEqual(workspace_scope_block([]), '')
        self.assertEqual(workspace_scope_block(None), '')

    def test_skips_blank_and_dot_entries(self) -> None:
        # Empty / "." entries are silently dropped — they signal
        # "no real path here" and would render as a useless bullet.
        self.assertEqual(workspace_scope_block(['', '.', None]), '')

    def test_renders_each_path_as_a_bullet(self) -> None:
        block = workspace_scope_block([
            '/Users/shay/.kato/workspaces/PROJ-1/client',
            '/Users/shay/.kato/workspaces/PROJ-1/backend',
        ])
        self.assertIn('STRICT BOUNDARY', block)
        self.assertIn('/Users/shay/.kato/workspaces/PROJ-1/client', block)
        self.assertIn('/Users/shay/.kato/workspaces/PROJ-1/backend', block)

    def test_explicitly_forbids_operator_source_clones(self) -> None:
        block = workspace_scope_block(['/x/workspace/client'])
        self.assertIn('REPOSITORY_ROOT_PATH', block)
        self.assertIn('Do NOT', block)

    def test_explicitly_forbids_other_tasks_workspaces(self) -> None:
        block = workspace_scope_block(['/x/workspace/client'])
        self.assertIn('other tasks', block.lower())
        self.assertIn('~/.kato/workspaces/', block)

    def test_explicitly_lists_mutating_tools(self) -> None:
        # Tool guardrail is concrete — names every kato-spawned tool
        # the agent might reach for so there's no ambiguity.
        block = workspace_scope_block(['/x/workspace/client'])
        for tool in ('Bash', 'Edit', 'Write', 'MultiEdit', 'Read', 'Grep', 'Glob'):
            self.assertIn(tool, block, msg=f'expected {tool} in scope block')

    def test_normalises_trailing_separators(self) -> None:
        # Trailing slashes shouldn't break path matching downstream.
        block = workspace_scope_block(['/x/workspace/client/'])
        self.assertIn('/x/workspace/client', block)
        self.assertNotIn('/x/workspace/client/\n', block)


class ImplementationPromptScopeTests(unittest.TestCase):
    """Implementation prompt prepends the scope block."""

    def _prepared_task(self, paths) -> PreparedTaskContext:
        return PreparedTaskContext(
            branch_name='feature/proj-1',
            repositories=[
                SimpleNamespace(id=f'repo-{i}', local_path=path)
                for i, path in enumerate(paths)
            ],
            repository_branches={f'repo-{i}': 'feature/proj-1' for i in range(len(paths))},
            agents_instructions='',
        )

    def test_implementation_prompt_starts_with_scope_block(self) -> None:
        client = ClaudeCliClient(binary='unused-builder-only')
        task = Task(id='PROJ-1', summary='do', description='things')
        prepared = self._prepared_task(['/x/workspaces/PROJ-1/client'])
        prompt = client._build_implementation_prompt(task, prepared)
        # First line is the boundary, ahead of "Implement task".
        self.assertTrue(prompt.startswith('WORKSPACE SCOPE'))
        # Boundary appears strictly before the task body.
        self.assertLess(
            prompt.index('STRICT BOUNDARY'),
            prompt.index('Implement task'),
        )

    def test_implementation_prompt_lists_every_repo_path(self) -> None:
        client = ClaudeCliClient(binary='unused-builder-only')
        task = Task(id='PROJ-1', summary='do', description='things')
        prepared = self._prepared_task([
            '/x/workspaces/PROJ-1/client',
            '/x/workspaces/PROJ-1/backend',
        ])
        prompt = client._build_implementation_prompt(task, prepared)
        self.assertIn('/x/workspaces/PROJ-1/client', prompt)
        self.assertIn('/x/workspaces/PROJ-1/backend', prompt)


class ReviewPromptScopeTests(unittest.TestCase):
    """Review-fix prompts (singular + batched, both modes) prepend the scope block."""

    def _comment(self, *, body: str = 'fix the typo') -> ReviewComment:
        c = ReviewComment(
            pull_request_id='17', comment_id='100',
            author='reviewer', body=body,
        )
        setattr(c, PullRequestFields.REPOSITORY_ID, 'client')
        return c

    def test_singular_review_prompt_starts_with_scope_block(self) -> None:
        prompt = ClaudeCliClient._build_review_prompt(
            self._comment(),
            'feature/proj-1',
            workspace_path='/x/workspaces/PROJ-1/client',
        )
        self.assertTrue(prompt.startswith('WORKSPACE SCOPE'))
        self.assertIn('/x/workspaces/PROJ-1/client', prompt)

    def test_singular_review_answer_mode_includes_scope_block(self) -> None:
        prompt = ClaudeCliClient._build_review_prompt(
            self._comment(body='how does this work?'),
            'feature/proj-1',
            workspace_path='/x/workspaces/PROJ-1/client',
            mode='answer',
        )
        self.assertTrue(prompt.startswith('WORKSPACE SCOPE'))

    def test_batched_review_prompt_starts_with_scope_block(self) -> None:
        prompt = ClaudeCliClient._build_review_comments_batch_prompt(
            [self._comment(), self._comment()],
            'feature/proj-1',
            workspace_path='/x/workspaces/PROJ-1/client',
        )
        self.assertTrue(prompt.startswith('WORKSPACE SCOPE'))

    def test_review_prompt_without_workspace_path_omits_scope_block(self) -> None:
        # No path known — block silently absent so we don't render
        # an empty boundary that confuses the model.
        prompt = ClaudeCliClient._build_review_prompt(
            self._comment(),
            'feature/proj-1',
            workspace_path='',
        )
        self.assertFalse(prompt.startswith('WORKSPACE SCOPE'))


if __name__ == '__main__':
    unittest.main()
