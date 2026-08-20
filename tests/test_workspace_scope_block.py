"""Tests for the workspace-scope boundary block + Kato refusal-guidance injection.

After the agent_core_lib consolidation the boundary block has two
layers:

1. ``agent_core_lib`` renders a GENERIC, product-agnostic strict
   boundary (``workspace_scope_block``) — it names only the allowed
   paths + the operator-config env vars, never any Kato workflow.
2. Kato owns the actionable refusal guidance (``kato:repo`` tags,
   YouTrack/Jira, the Files-tab sync) and injects it through the
   ``workspace_refusal_guidance`` client param so the safer wording
   reaches Kato production prompts WITHOUT agent_core_lib knowing about
   Kato.

These tests pin: (1) the generic block has no Kato wording by default;
(2) ``extra_refusal_guidance`` is appended when provided; (3) the
Kato/Claude prompt path includes the Kato guidance when wired; (4) a
client with no guidance is unchanged.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from agent_core_lib.agent_core_lib.helpers.agent_prompt_utils import workspace_scope_block
from claude_core_lib.claude_core_lib.cli_client import ClaudeCliClient
from kato_core_lib.data_layers.data.fields import PullRequestFields
from kato_core_lib.data_layers.data.task import Task
from kato_core_lib.helpers.task_context_utils import PreparedTaskContext
from kato_core_lib.helpers.workspace_refusal_guidance import (
    KATO_WORKSPACE_REFUSAL_GUIDANCE,
)
from provider_client_base.provider_client_base.data.review_comment import ReviewComment


# Kato-specific tokens that must NEVER appear in the product-agnostic
# agent_core_lib block by default.
_KATO_TOKENS = (
    'kato:repo:', 'YouTrack', 'Files tab', 'Sync repositories', 'WHEN YOU MUST REFUSE',
    'KATO_WORKSPACES_ROOT', 'KATO_IGNORED_REPOSITORY_FOLDERS', 'KATO_REPOSITORY_ROOT_PATH',
)


class WorkspaceScopeBlockGenericTests(unittest.TestCase):
    """The generic agent_core_lib block — content, edges, product-agnosticism."""

    def test_empty_path_list_returns_empty_string(self) -> None:
        self.assertEqual(workspace_scope_block([]), '')
        self.assertEqual(workspace_scope_block(None), '')

    def test_skips_blank_and_dot_entries(self) -> None:
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
        self.assertIn('AGENT_REPOSITORY_ROOT_PATH', block)
        self.assertIn('Do NOT', block)

    def test_explicitly_forbids_other_tasks_workspaces(self) -> None:
        block = workspace_scope_block(['/x/workspace/client'])
        self.assertIn('other tasks', block.lower())
        self.assertIn('AGENT_WORKSPACES_ROOT', block)

    def test_explicitly_lists_mutating_tools(self) -> None:
        block = workspace_scope_block(['/x/workspace/client'])
        for tool in ('Bash', 'Edit', 'Write', 'MultiEdit', 'Read', 'Grep', 'Glob'):
            self.assertIn(tool, block, msg=f'expected {tool} in scope block')

    def test_normalises_trailing_separators(self) -> None:
        block = workspace_scope_block(['/x/workspace/client/'])
        self.assertIn('/x/workspace/client', block)
        self.assertNotIn('/x/workspace/client/\n', block)

    # (1) Generic block has NO Kato-specific wording by default.
    def test_default_block_has_no_kato_specific_wording(self) -> None:
        block = workspace_scope_block(['/x/workspaces/PROJ-1/client'])
        for token in _KATO_TOKENS:
            self.assertNotIn(token, block, msg=f'agent_core_lib block leaked {token!r}')


class ExtraRefusalGuidanceParamTests(unittest.TestCase):
    """(2) ``extra_refusal_guidance`` is appended only when provided."""

    def test_no_guidance_appended_by_default(self) -> None:
        block = workspace_scope_block(['/x/workspaces/PROJ-1/client'])
        self.assertNotIn('SENTINEL-REFUSAL-GUIDANCE', block)

    def test_guidance_appended_after_the_generic_refusal(self) -> None:
        sentinel = 'SENTINEL-REFUSAL-GUIDANCE: widen scope via X'
        block = workspace_scope_block(
            ['/x/workspaces/PROJ-1/client'],
            extra_refusal_guidance=sentinel,
        )
        self.assertIn(sentinel, block)
        # Appended AFTER the generic boundary/refusal text.
        self.assertLess(block.index('STRICT BOUNDARY'), block.index(sentinel))
        # The generic refusal sentence now ends with the ASK instruction
        # rather than "reaching for it" — the product guidance still comes
        # after it, which is what this test is actually about.
        self.assertLess(
            block.index('STOP and ASK IN THE CHAT'), block.index(sentinel),
        )

    def test_blank_guidance_is_ignored(self) -> None:
        base = workspace_scope_block(['/x/workspaces/PROJ-1/client'])
        self.assertEqual(
            base,
            workspace_scope_block(['/x/workspaces/PROJ-1/client'], extra_refusal_guidance='   '),
        )

    def test_empty_paths_emit_nothing_even_with_guidance(self) -> None:
        self.assertEqual(workspace_scope_block([], extra_refusal_guidance='x'), '')


class KatoRefusalGuidanceContentTests(unittest.TestCase):
    """The Kato-owned guidance text carries the full actionable template."""

    def test_guidance_has_the_actionable_template(self) -> None:
        g = KATO_WORKSPACE_REFUSAL_GUIDANCE
        self.assertIn('WHEN YOU MUST REFUSE', g)
        self.assertIn('Do not just say', g)
        self.assertIn('<requested-path>', g)
        self.assertIn('Check the task tags', g)
        self.assertIn('kato:repo:', g)
        self.assertIn('Tag is missing', g)
        self.assertIn('Sync repositories', g)
        self.assertIn('Tag is already there', g)
        self.assertIn('close + reopen', g)
        self.assertIn('OLD set of repos', g)
        self.assertIn('multi-repo', g)
        self.assertIn('Once my session restarts', g)


class KatoPromptPathInjectionTests(unittest.TestCase):
    """(3)/(4) The Kato/Claude prompt path includes the guidance ONLY when wired."""

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

    def _comment(self, *, body: str = 'fix the typo') -> ReviewComment:
        c = ReviewComment(
            pull_request_id='17', comment_id='100', author='reviewer', body=body,
        )
        setattr(c, PullRequestFields.REPOSITORY_ID, 'client')
        return c

    # (3) Kato wires the guidance → the production prompt carries it.
    def test_client_with_kato_guidance_includes_refusal_template(self) -> None:
        client = ClaudeCliClient(
            binary='unused-builder-only',
            workspace_refusal_guidance=KATO_WORKSPACE_REFUSAL_GUIDANCE,
        )
        task = Task(id='PROJ-1', summary='do', description='things')
        prepared = self._prepared_task(['/x/workspaces/PROJ-1/client'])
        prompt = client._build_implementation_prompt(task, prepared)
        self.assertTrue(prompt.startswith('WORKSPACE SCOPE'))
        self.assertIn('WHEN YOU MUST REFUSE', prompt)
        self.assertIn('kato:repo:', prompt)
        self.assertIn('Sync repositories', prompt)

    def test_review_prompt_with_kato_guidance_includes_refusal_template(self) -> None:
        # The review builders are classmethods; the instance review flow
        # threads ``self._workspace_refusal_guidance`` into them, so pass
        # it explicitly here to exercise the same param.
        prompt = ClaudeCliClient._build_review_prompt(
            self._comment(),
            'feature/proj-1',
            workspace_path='/x/workspaces/PROJ-1/client',
            workspace_refusal_guidance=KATO_WORKSPACE_REFUSAL_GUIDANCE,
        )
        self.assertTrue(prompt.startswith('WORKSPACE SCOPE'))
        self.assertIn('WHEN YOU MUST REFUSE', prompt)

    # (4) Default client (no guidance) is unchanged — generic block only.
    def test_client_without_guidance_has_no_refusal_template(self) -> None:
        client = ClaudeCliClient(binary='unused-builder-only')
        task = Task(id='PROJ-1', summary='do', description='things')
        prepared = self._prepared_task(['/x/workspaces/PROJ-1/client'])
        prompt = client._build_implementation_prompt(task, prepared)
        # Generic boundary still leads the prompt...
        self.assertTrue(prompt.startswith('WORKSPACE SCOPE'))
        self.assertLess(prompt.index('STRICT BOUNDARY'), prompt.index('Implement task'))
        self.assertIn('/x/workspaces/PROJ-1/client', prompt)
        # ...but no Kato-specific refusal template leaks in.
        self.assertNotIn('WHEN YOU MUST REFUSE', prompt)
        self.assertNotIn('kato:repo:', prompt)

    def test_review_prompt_without_workspace_path_omits_scope_block(self) -> None:
        prompt = ClaudeCliClient._build_review_prompt(
            self._comment(),
            'feature/proj-1',
            workspace_path='',
        )
        self.assertFalse(prompt.startswith('WORKSPACE SCOPE'))


class MultiBackendGuidanceParityTests(unittest.TestCase):
    """The same Kato guidance reaches Codex + OpenHands prompts the same way
    it reaches Claude — and every backend stays Kato-free by default."""

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

    def _task(self) -> Task:
        return Task(id='PROJ-1', summary='do', description='things')

    def _codex(self, **overrides):
        from codex_core_lib.codex_core_lib.cli_client import CodexCliClient
        return CodexCliClient(binary='unused-builder-only', **overrides)

    def _openhands(self, **overrides):
        from openhands_core_lib.openhands_core_lib.openhands_client import OpenHandsClient
        return OpenHandsClient('http://localhost', 'unused-key', **overrides)

    # Codex/OpenHands lead with their own filesystem-scope preamble, so the
    # boundary block is PRESENT (assertIn) rather than first (Claude's contract).
    def test_codex_includes_kato_guidance_when_wired(self) -> None:
        client = self._codex(workspace_refusal_guidance=KATO_WORKSPACE_REFUSAL_GUIDANCE)
        prompt = client._build_implementation_prompt(
            self._task(), self._prepared_task(['/x/workspaces/PROJ-1/client']),
        )
        self.assertIn('WORKSPACE SCOPE', prompt)
        self.assertIn('WHEN YOU MUST REFUSE', prompt)
        self.assertIn('kato:repo:', prompt)

    def test_codex_default_prompt_stays_kato_free(self) -> None:
        prompt = self._codex()._build_implementation_prompt(
            self._task(), self._prepared_task(['/x/workspaces/PROJ-1/client']),
        )
        self.assertIn('WORKSPACE SCOPE', prompt)
        self.assertNotIn('WHEN YOU MUST REFUSE', prompt)
        self.assertNotIn('kato:repo:', prompt)

    def test_openhands_includes_kato_guidance_when_wired(self) -> None:
        client = self._openhands(workspace_refusal_guidance=KATO_WORKSPACE_REFUSAL_GUIDANCE)
        prompt = client._build_implementation_prompt(
            self._task(), self._prepared_task(['/x/workspaces/PROJ-1/client']),
        )
        self.assertIn('WORKSPACE SCOPE', prompt)
        self.assertIn('WHEN YOU MUST REFUSE', prompt)
        self.assertIn('kato:repo:', prompt)

    def test_openhands_default_prompt_stays_kato_free(self) -> None:
        prompt = self._openhands()._build_implementation_prompt(
            self._task(), self._prepared_task(['/x/workspaces/PROJ-1/client']),
        )
        self.assertIn('WORKSPACE SCOPE', prompt)
        self.assertNotIn('WHEN YOU MUST REFUSE', prompt)
        self.assertNotIn('kato:repo:', prompt)


if __name__ == '__main__':
    unittest.main()


class TheBoundaryIsNamedAndAbsoluteTests(unittest.TestCase):
    """The first prompt has to name the task folder and mean it.

    A list of allowed directories reads as "here are some places you may
    work". The operator's requirement is a wall: one named root, never
    leave it, and if something is missing ASK rather than go looking.
    That has to be the first thing in the block — it is the line that
    survives a long prompt.
    """

    ROOT = '/Users/dev/.kato/workspaces/PROJ-123'

    def _block(self) -> str:
        return workspace_scope_block([self.ROOT])

    def test_the_task_folder_is_named_up_front(self) -> None:
        block = self._block()
        self.assertIn(f'YOUR TASK FOLDER IS: {self.ROOT}', block)
        # First 200 chars — not buried four paragraphs down.
        self.assertIn(self.ROOT, block[:250])

    def test_it_says_never_leave_it(self) -> None:
        self.assertIn('NEVER go outside it', self._block())

    def test_it_says_to_ask_in_the_chat_rather_than_go_looking(self) -> None:
        block = self._block()
        self.assertIn('ASK FOR IT IN THE CHAT', block)
        self.assertIn('Do not go and find it', block)

    def test_it_covers_every_file_operation_not_just_writes(self) -> None:
        block = self._block()
        for verb in ('reading', 'writing', 'editing', 'creating',
                     'renaming', 'deleting', 'listing', 'searching'):
            self.assertIn(verb, block)

    def test_the_agents_OWN_files_are_covered_too(self) -> None:
        # ``.claude``, memory/context files, scratch notes — the operator
        # called these out specifically: if the agent wants it to exist,
        # it goes inside the task folder, not the home directory.
        block = self._block()
        self.assertIn('.claude', block)
        self.assertIn('memory', block)
        self.assertIn('home directory', block)

    def test_asking_is_still_the_instruction_at_the_end_of_the_block(self) -> None:
        # The closing paragraph used to say "stop and report it", which an
        # agent can satisfy by writing a sentence and carrying on without
        # what it needed.
        block = self._block()
        self.assertIn('STOP and ASK IN THE CHAT', block)
        self.assertIn('do not silently do', block)

    def test_a_multi_repo_task_still_names_ONE_root(self) -> None:
        block = workspace_scope_block([
            f'{self.ROOT}/client', f'{self.ROOT}/backend',
        ])
        self.assertIn('YOUR TASK FOLDER IS:', block)
        self.assertIn(f'{self.ROOT}/client', block)
        self.assertIn(f'{self.ROOT}/backend', block)

    def test_no_paths_still_renders_nothing(self) -> None:
        self.assertEqual(workspace_scope_block([]), '')
