"""Tests for sandbox_core_lib.system_prompt.

Verifies compose_system_prompt assembles sections in the correct order,
conditionally includes the sandbox addendum only for docker mode, and
preserves all three constant addendum strings intact.
"""
from __future__ import annotations

import unittest

from sandbox_core_lib.sandbox_core_lib.system_prompt import (
    RESUMED_SESSION_ADDENDUM,
    SANDBOX_SYSTEM_PROMPT_ADDENDUM,
    WORKSPACE_SCOPE_ADDENDUM,
    compose_system_prompt,
)


class SystemPromptConstantsTests(unittest.TestCase):
    def test_workspace_scope_addendum_mentions_rg_and_grep(self):
        self.assertIn('rg', WORKSPACE_SCOPE_ADDENDUM)
        self.assertIn('grep', WORKSPACE_SCOPE_ADDENDUM)

    def test_workspace_scope_addendum_warns_against_find_slash(self):
        self.assertIn('find /', WORKSPACE_SCOPE_ADDENDUM)

    def test_resumed_session_addendum_mentions_conversation_history(self):
        self.assertIn('conversation', RESUMED_SESSION_ADDENDUM.lower())

    def test_sandbox_addendum_mentions_filesystem_constraint(self):
        self.assertIn('Filesystem', SANDBOX_SYSTEM_PROMPT_ADDENDUM)

    def test_sandbox_addendum_mentions_network_restriction(self):
        self.assertIn('api.anthropic.com', SANDBOX_SYSTEM_PROMPT_ADDENDUM)

    def test_sandbox_addendum_mentions_privilege_drop(self):
        self.assertIn('non-root', SANDBOX_SYSTEM_PROMPT_ADDENDUM)

    def test_sandbox_addendum_mentions_untrusted_workspace_tag(self):
        self.assertIn('UNTRUSTED_WORKSPACE_FILE', SANDBOX_SYSTEM_PROMPT_ADDENDUM)

    def test_sandbox_addendum_warns_against_prompt_injection(self):
        self.assertIn('prompt-injection', SANDBOX_SYSTEM_PROMPT_ADDENDUM)

    def test_workspace_scope_addendum_warns_against_find_tilde(self):
        self.assertIn('find ~', WORKSPACE_SCOPE_ADDENDUM)


class ComposeSystemPromptTests(unittest.TestCase):
    def test_docker_off_excludes_sandbox_addendum(self):
        result = compose_system_prompt('', docker_mode_on=False)
        self.assertNotIn(SANDBOX_SYSTEM_PROMPT_ADDENDUM, result)

    def test_docker_on_includes_sandbox_addendum(self):
        result = compose_system_prompt('', docker_mode_on=True)
        self.assertIn(SANDBOX_SYSTEM_PROMPT_ADDENDUM, result)

    def test_always_includes_workspace_scope_addendum(self):
        result_off = compose_system_prompt('', docker_mode_on=False)
        result_on = compose_system_prompt('', docker_mode_on=True)
        self.assertIn(WORKSPACE_SCOPE_ADDENDUM, result_off)
        self.assertIn(WORKSPACE_SCOPE_ADDENDUM, result_on)

    def test_always_includes_resumed_session_addendum(self):
        result_off = compose_system_prompt('', docker_mode_on=False)
        result_on = compose_system_prompt('', docker_mode_on=True)
        self.assertIn(RESUMED_SESSION_ADDENDUM, result_off)
        self.assertIn(RESUMED_SESSION_ADDENDUM, result_on)

    def test_architecture_doc_included_when_provided(self):
        arch = '# Architecture\nThis is the arch doc.'
        result = compose_system_prompt(arch, docker_mode_on=False)
        self.assertIn(arch, result)

    def test_architecture_doc_excluded_when_empty(self):
        result = compose_system_prompt('', docker_mode_on=False)
        self.assertNotIn('# Architecture', result)

    def test_lessons_included_when_provided(self):
        lessons = 'Always write tests first.'
        result = compose_system_prompt('', docker_mode_on=False, lessons=lessons)
        self.assertIn(lessons, result)

    def test_lessons_excluded_when_empty(self):
        result = compose_system_prompt('', docker_mode_on=False, lessons='')
        # Both standard addenda present, no arch or lessons content added
        self.assertIn(WORKSPACE_SCOPE_ADDENDUM, result)
        self.assertIn(RESUMED_SESSION_ADDENDUM, result)
        self.assertNotIn('# Architecture', result)
        self.assertNotIn(SANDBOX_SYSTEM_PROMPT_ADDENDUM, result)

    def test_sections_joined_with_double_newline(self):
        result = compose_system_prompt('Arch.', docker_mode_on=False)
        self.assertIn('\n\n', result)

    def test_order_arch_then_lessons_then_workspace_scope(self):
        arch = 'Arch doc content'
        lessons = 'Lessons content'
        result = compose_system_prompt(arch, docker_mode_on=False, lessons=lessons)
        arch_pos = result.index(arch)
        lessons_pos = result.index(lessons)
        scope_pos = result.index(WORKSPACE_SCOPE_ADDENDUM)
        self.assertLess(arch_pos, lessons_pos)
        self.assertLess(lessons_pos, scope_pos)

    def test_order_workspace_scope_before_sandbox_when_docker_on(self):
        result = compose_system_prompt('', docker_mode_on=True)
        scope_pos = result.index(WORKSPACE_SCOPE_ADDENDUM)
        sandbox_pos = result.index(SANDBOX_SYSTEM_PROMPT_ADDENDUM)
        self.assertLess(scope_pos, sandbox_pos)

    def test_none_architecture_treated_as_empty(self):
        result = compose_system_prompt(None, docker_mode_on=False)  # type: ignore[arg-type]
        self.assertIn(WORKSPACE_SCOPE_ADDENDUM, result)

    def test_none_lessons_treated_as_empty(self):
        result = compose_system_prompt('', docker_mode_on=False, lessons=None)  # type: ignore[arg-type]
        self.assertIn(WORKSPACE_SCOPE_ADDENDUM, result)

    def test_all_parts_present_when_docker_on_and_arch_and_lessons(self):
        arch = 'Architecture section.'
        lessons = 'Prior lessons.'
        result = compose_system_prompt(arch, docker_mode_on=True, lessons=lessons)
        self.assertIn(arch, result)
        self.assertIn(lessons, result)
        self.assertIn(WORKSPACE_SCOPE_ADDENDUM, result)
        self.assertIn(RESUMED_SESSION_ADDENDUM, result)
        self.assertIn(SANDBOX_SYSTEM_PROMPT_ADDENDUM, result)
