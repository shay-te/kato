"""Targeted branch-coverage tests for ``agent_prompt_utils``.

Each test names the specific gap it closes so future readers know
why the case looks deliberately narrow.
"""
from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent_core_lib.agent_core_lib.helpers.agent_prompt_utils import (
    IGNORED_REPOSITORY_FOLDERS_ENV,
    _collapse_redundant_scope_paths,
    forbidden_repository_guardrails_text,
    ignored_repository_folder_names,
    repository_scope_text,
    workspace_inventory_block,
    workspace_scope_block,
)


class IgnoredFoldersEnvNameTests(unittest.TestCase):
    """The ignored-folders env read uses the generic name only (no
    product-specific env var), and the rendered guardrails/scope text uses
    generic wording by default."""

    def _isolated_env(self):
        # patch.dict snapshots + restores os.environ; inside we drop the key
        # so the host's real environment can't pollute the case.
        ctx = patch.dict(os.environ, {}, clear=False)
        ctx.start()
        os.environ.pop(IGNORED_REPOSITORY_FOLDERS_ENV, None)
        self.addCleanup(ctx.stop)

    def test_generic_env_var_is_read(self) -> None:
        self._isolated_env()
        os.environ[IGNORED_REPOSITORY_FOLDERS_ENV] = 'a, b'
        self.assertEqual(ignored_repository_folder_names(), ['a', 'b'])

    def test_canonical_constant_is_the_generic_name(self) -> None:
        self.assertEqual(IGNORED_REPOSITORY_FOLDERS_ENV, 'AGENT_IGNORED_REPOSITORY_FOLDERS')

    def test_rendered_text_uses_generic_wording_by_default(self) -> None:
        # The rendered text names the generic AGENT_* env vars (a
        # product-specific name would replace these tokens).
        guardrails = forbidden_repository_guardrails_text('secret-client, legacy-api')
        self.assertIn('AGENT_IGNORED_REPOSITORY_FOLDERS', guardrails)

        block = workspace_scope_block(['/wks/PROJ/repo-a'])
        self.assertIn('AGENT_WORKSPACES_ROOT', block)
        self.assertIn('AGENT_REPOSITORY_ROOT_PATH', block)


class WorkspaceInventoryBlockBranchTests(unittest.TestCase):
    def test_extras_only_render_without_cwd_header(self) -> None:
        # Branch 73->75: ``cwd_text`` is empty so the ``(cwd)`` line is
        # skipped and the renderer goes straight to the extras loop.
        # Tasks created before the workspace is provisioned hit this
        # path — the inventory still anchors the agent to the extras.
        block = workspace_inventory_block(
            cwd='', additional_dirs=['/wks/PROJ/repo-a', '/wks/PROJ/repo-b'],
        )
        self.assertIn('Repositories available in this workspace:', block)
        self.assertNotIn('(cwd)', block)
        self.assertIn('- /wks/PROJ/repo-a', block)
        self.assertIn('- /wks/PROJ/repo-b', block)

    def test_multi_folder_frees_the_agent_from_the_cwd_repo(self) -> None:
        # The agent tended to stay anchored in the (cwd) repo and never touch a
        # sibling — so a multi-repo task got worked in only one repo. When more
        # than one folder is present, the inventory must explicitly free it to
        # work across all of them.
        block = workspace_inventory_block(
            cwd='/wks/PROJ/repo-a', additional_dirs=['/wks/PROJ'],
        )
        self.assertIn('only where your shell starts', block)
        self.assertIn('does NOT', block)
        self.assertIn('sibling', block)

    def test_single_folder_omits_the_cross_repo_note(self) -> None:
        # With only the cwd (single repo) there is nowhere else to move; the
        # "work across folders" note would be noise, so it is omitted.
        block = workspace_inventory_block(cwd='/wks/PROJ/repo-a', additional_dirs=None)
        self.assertNotIn('only where your shell starts', block)


class WorkspaceScopeBlockBranchTests(unittest.TestCase):
    def test_skips_paths_that_normalize_to_dot_or_blank(self) -> None:
        # Branch 155->151: ``normalized`` is empty or just '.' — fall
        # through without appending and loop to the next raw entry.
        # Without coverage here, a stray '.' in the caller's allowed-
        # paths config would silently render a malformed scope block.
        # Mixing in a real path proves the loop continues correctly.
        block = workspace_scope_block(['.', '', '/wks/PROJ/repo-a'])
        self.assertIn('/wks/PROJ/repo-a', block)
        # The bullet list shouldn't contain a lone '.' or empty line.
        self.assertNotIn('  - .\n', block)
        self.assertNotIn('  - \n', block)

    def test_returns_empty_when_every_path_is_filtered(self) -> None:
        # Same branch (155->151), but the filter takes EVERY path so
        # ``paths`` stays empty and the function short-circuits to ''.
        self.assertEqual(workspace_scope_block(['.', '', None]), '')

    def test_collapses_a_repo_path_nested_under_the_workspace_root(self) -> None:
        # UNA-2763 production report: review-fix's scope block listed
        # BOTH the comment's own repo AND the task's whole workspace
        # folder (which already contains it) as two separate, seemingly
        # independent bullets. The narrower, already-covered path must
        # not appear once its ancestor is also in the list.
        block = workspace_scope_block([
            '/wks/UNA-2763/ob-love-admin-client', '/wks/UNA-2763',
        ])
        self.assertIn('  - /wks/UNA-2763\n', block)
        self.assertNotIn('ob-love-admin-client', block)

    def test_does_not_collapse_unrelated_sibling_paths(self) -> None:
        # Two repos that don't nest inside each other must BOTH stay —
        # this is the legitimate multi-repo case, not redundancy.
        block = workspace_scope_block([
            '/wks/UNA-2763/ob-love-admin-client',
            '/wks/UNA-2763/library-core-lib',
        ])
        self.assertIn('ob-love-admin-client', block)
        self.assertIn('library-core-lib', block)


class CollapseRedundantScopePathsTests(unittest.TestCase):
    """Direct unit coverage for the helper, isolating it from
    workspace_scope_block's rendering so the SAFETY boundary — never
    inventing a shared-parent path the caller didn't explicitly list —
    is pinned independently of the prose around it.
    """

    def test_drops_a_path_nested_under_another(self) -> None:
        result = _collapse_redundant_scope_paths([
            '/wks/T1/repo-a', '/wks/T1',
        ])
        self.assertEqual(result, ['/wks/T1'])

    def test_order_of_ancestor_and_descendant_does_not_matter(self) -> None:
        result = _collapse_redundant_scope_paths([
            '/wks/T1', '/wks/T1/repo-a',
        ])
        self.assertEqual(result, ['/wks/T1'])

    def test_drops_multiple_descendants_of_the_same_ancestor(self) -> None:
        result = _collapse_redundant_scope_paths([
            '/wks/T1/repo-a', '/wks/T1/repo-b', '/wks/T1',
        ])
        self.assertEqual(result, ['/wks/T1'])

    def test_never_invents_a_shared_parent_that_was_not_explicitly_listed(self) -> None:
        # SAFETY: two repos sharing a parent directory that is NOT
        # itself in the list must NOT collapse to that parent — it
        # could be the operator's entire configured repository root
        # (every task's, every repo's checkout), not something scoped
        # to this one task.
        result = _collapse_redundant_scope_paths([
            '/repos/admin-client', '/repos/admin-backend',
        ])
        self.assertEqual(
            sorted(result), ['/repos/admin-backend', '/repos/admin-client'],
        )

    def test_single_path_is_returned_unchanged(self) -> None:
        self.assertEqual(
            _collapse_redundant_scope_paths(['/wks/T1']), ['/wks/T1'],
        )

    def test_empty_list_is_returned_unchanged(self) -> None:
        self.assertEqual(_collapse_redundant_scope_paths([]), [])

    def test_a_path_that_merely_shares_a_string_prefix_is_not_treated_as_nested(self) -> None:
        # '/wks/T1-extra' is NOT inside '/wks/T1' — a naive
        # startswith('/wks/T1') (no separator) would wrongly say it is.
        result = _collapse_redundant_scope_paths([
            '/wks/T1', '/wks/T1-extra',
        ])
        self.assertEqual(sorted(result), ['/wks/T1', '/wks/T1-extra'])


class RepositoryScopeTextBranchTests(unittest.TestCase):
    def test_prepared_task_without_branch_name_keeps_task_branch(self) -> None:
        # Branch 198->203: ``prepared_task.branch_name`` is falsy — the
        # ``if`` body is skipped and we fall straight through to the
        # ``if not repositories`` check (line 203). The task's own
        # ``branch_name`` must survive the prepared-task override.
        task = SimpleNamespace(
            id='PROJ-1',
            branch_name='task-branch',
            repository_branches={},
            repositories=[],
        )
        prepared = SimpleNamespace(
            repositories=[],
            repository_branches={},
            branch_name='',  # falsy — branch override skipped
        )
        out = repository_scope_text(task, prepared)
        # No repositories → falls into the "before making changes"
        # template which embeds the resolved branch name. The
        # branch name should be the TASK's branch, not the empty
        # prepared one.
        self.assertIn('task-branch', out)
        self.assertIn('Before making changes', out)


if __name__ == '__main__':
    unittest.main()


class HelperScriptDirectoryTests(unittest.TestCase):
    """The scope block names WHERE scripts go, not just where they don't.

    The block always forbade ``/tmp``, and agents kept writing scripts there
    anyway: a prohibition with no destination loses to habit, because an agent
    that needs somewhere to put a one-off script still needs somewhere. Naming
    the place is the fix.
    """

    def test_it_names_a_helper_script_directory(self) -> None:
        block = workspace_scope_block(['/wks/PROJ-1'])
        self.assertIn('YOUR HELPER-SCRIPT DIRECTORY IS:', block)
        self.assertIn('/wks/PROJ-1/helper_scripts', block)

    def test_it_sits_beside_the_repositories_not_inside_one(self) -> None:
        # Anything inside a repository clone is swept into the task's commit
        # by the publishing side, so a scratch dir there would ship the
        # agent's junk in the pull request. The collapsed parent is the task
        # folder; that is where it must land.
        block = workspace_scope_block(['/wks/PROJ-1/repo-a', '/wks/PROJ-1'])
        self.assertIn('/wks/PROJ-1/helper_scripts', block)
        self.assertNotIn('/wks/PROJ-1/repo-a/helper_scripts', block)

    def test_it_tells_the_agent_NOT_to_delete_its_own_scripts(self) -> None:
        # Not merely "you needn't tidy": keeping them is the instruction. A
        # helper written three turns ago is usually the thing needed two
        # turns later, and the workspace removal is the only cleanup there
        # is, so deleting early only costs a rewrite.
        block = workspace_scope_block(['/wks/PROJ-1'])
        self.assertIn('Do NOT delete anything from it', block)
        self.assertIn('need again', block)
        self.assertIn('removes this task', block)

    def test_it_covers_what_scripts_PRODUCE_not_just_scripts(self) -> None:
        # Measured from real sessions: the agent put test databases
        # (/tmp/*_tests.db), backups (/tmp/*.bak) and its own notes outside
        # the workspace, not only scripts. Naming scripts alone leaves every
        # one of those without a home.
        block = workspace_scope_block(['/wks/PROJ-1'])
        for need in ('test databases', 'backups', 'notes'):
            self.assertIn(need, block)

    def test_running_installed_tools_is_not_a_boundary_violation(self) -> None:
        # The old wording ("NEVER go outside it — not to read, not to
        # write, not to list") also forbade ``2>/dev/null`` and running the
        # system python. A rule that cannot be followed gets discounted
        # wholesale, so the carve-out is what keeps the rest credible.
        block = workspace_scope_block(['/wks/PROJ-1'])
        self.assertIn('/dev/null', block)
        self.assertIn('PROJECT', block)

    def test_it_says_to_ask_for_a_repository_rather_than_find_it(self) -> None:
        # Measured: 4 Edits and 4 Reads landed in the operator's own source
        # tree — the agent went looking for a repo the task did not have.
        block = workspace_scope_block(['/wks/PROJ-1'])
        self.assertIn('Ask for the repository to be added', block)

    def test_the_forbidden_locations_are_still_spelled_out(self) -> None:
        block = workspace_scope_block(['/wks/PROJ-1'])
        for path in ('/tmp', '/var/tmp', '$TMPDIR'):
            self.assertIn(path, block)

    def test_no_directory_line_without_a_resolved_workspace(self) -> None:
        self.assertEqual(workspace_scope_block([]), '')
