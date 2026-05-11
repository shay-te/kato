"""Tests for the ``KATO_CLAUDE_ALLOWED_READ_ONLY_TOOLS`` startup gate.

The flag pre-approves a hardcoded list of read-only Bash commands
(grep / rg / ls / cat / find / head / tail / wc / file / stat) plus
the Read tool, so the operator isn't prompted for them.

The constraint locked here:

  * Read-only pre-approval is *only* meaningful inside the sandbox
    boundary. Without ``KATO_CLAUDE_DOCKER=true``, ``grep -r AWS_SECRET ~``
    runs on the operator's host with their file-system access. The
    sandbox is the prerequisite for letting any prompt be skipped.

So the validator refuses the read-only flag unless docker is also on.
Independent of ``KATO_CLAUDE_BYPASS_PERMISSIONS`` — the read-only
opt-in works whether bypass is on or off.
"""

from __future__ import annotations

import unittest

from sandbox_core_lib.sandbox_core_lib.bypass_permissions_validator import (
    BypassPermissionsRefused,
    READ_ONLY_TOOLS_ALLOWLIST,
    READ_ONLY_TOOLS_ENV_KEY,
    is_read_only_tools_enabled,
    validate_read_only_tools_requires_docker,
)


class IsReadOnlyToolsEnabledTests(unittest.TestCase):
    def test_unset_means_disabled(self) -> None:
        self.assertFalse(is_read_only_tools_enabled({}))

    def test_false_string_means_disabled(self) -> None:
        self.assertFalse(is_read_only_tools_enabled(
            {READ_ONLY_TOOLS_ENV_KEY: 'false'}
        ))

    def test_true_string_means_enabled(self) -> None:
        self.assertTrue(is_read_only_tools_enabled(
            {READ_ONLY_TOOLS_ENV_KEY: 'true'}
        ))

    def test_other_truthy_strings_means_enabled(self) -> None:
        # ``TRUE_VALUES`` accepts 1/true/yes/on for symmetry with the
        # other flag predicates in this module.
        for truthy in ('1', 'true', 'yes', 'on', 'TRUE', 'Yes'):
            self.assertTrue(
                is_read_only_tools_enabled({READ_ONLY_TOOLS_ENV_KEY: truthy}),
                f'expected {truthy!r} to be truthy',
            )


class ValidateReadOnlyToolsRequiresDockerTests(unittest.TestCase):
    def test_silent_when_flag_off(self) -> None:
        # No flag, no docker -> nothing to enforce.
        validate_read_only_tools_requires_docker(env={})

    def test_silent_when_both_flags_set(self) -> None:
        # The valid combination: docker provides the boundary,
        # read-only opts into pre-approval inside it.
        validate_read_only_tools_requires_docker(env={
            READ_ONLY_TOOLS_ENV_KEY: 'true',
            'KATO_CLAUDE_DOCKER': 'true',
        })

    def test_silent_when_only_docker_set(self) -> None:
        # Docker without read-only is the recommended belt+suspenders
        # mode — every tool still prompts. Read-only flag absent =
        # nothing for this validator to do.
        validate_read_only_tools_requires_docker(env={
            'KATO_CLAUDE_DOCKER': 'true',
        })

    def test_refuses_when_flag_set_without_docker(self) -> None:
        with self.assertRaises(BypassPermissionsRefused) as cm:
            validate_read_only_tools_requires_docker(env={
                READ_ONLY_TOOLS_ENV_KEY: 'true',
            })
        message = str(cm.exception)
        # Names the env var the operator set so they know which
        # flag the failure is about.
        self.assertIn(READ_ONLY_TOOLS_ENV_KEY, message)
        # Names the fix verbatim so a copy-paste resolves the error.
        self.assertIn('export KATO_CLAUDE_DOCKER=true', message)
        # Names the threat in concrete terms — a generic "this is
        # unsafe" message is easier to ignore than a specific
        # "grep can read your SSH key" one.
        self.assertTrue(
            'SSH' in message or 'ssh' in message or 'secret' in message.lower(),
            f'expected message to name the threat concretely, got: {message}',
        )

    def test_silent_when_read_only_and_bypass_both_set_with_docker(self) -> None:
        # The read-only flag is independent of bypass; when bypass
        # is also on, every tool is pre-approved already, so the
        # read-only flag is redundant but not refused. Lock that
        # the validator does not interpret bypass as a substitute
        # for docker — docker is the structural boundary, bypass
        # is the prompt-disable layer.
        validate_read_only_tools_requires_docker(env={
            READ_ONLY_TOOLS_ENV_KEY: 'true',
            'KATO_CLAUDE_DOCKER': 'true',
            'KATO_CLAUDE_BYPASS_PERMISSIONS': 'true',
        })

    def test_refuses_with_bypass_set_but_no_docker(self) -> None:
        # Bypass without docker is itself refused by a different
        # validator (``validate_bypass_permissions``). Even so, this
        # validator must not silently accept the read-only flag just
        # because bypass is on — bypass is not a substitute for
        # docker. (In production both validators run; this checks
        # the read-only one in isolation does the right thing.)
        with self.assertRaises(BypassPermissionsRefused):
            validate_read_only_tools_requires_docker(env={
                READ_ONLY_TOOLS_ENV_KEY: 'true',
                'KATO_CLAUDE_BYPASS_PERMISSIONS': 'true',
            })


class ReadOnlyToolsAllowlistShapeTests(unittest.TestCase):
    """Sanity checks on the hardcoded allowlist constant.

    These are not the drift-guard test (that one lives in
    ``test_open_gap_closures_doc_consistency.py`` and pins the exact
    membership). These tests just verify the constant is well-shaped
    so we catch obvious typos at import time.
    """

    def test_allowlist_is_frozenset(self) -> None:
        # Frozenset — operator code can't mutate this at runtime.
        self.assertIsInstance(READ_ONLY_TOOLS_ALLOWLIST, frozenset)

    def test_allowlist_is_non_empty(self) -> None:
        self.assertGreater(len(READ_ONLY_TOOLS_ALLOWLIST), 0)

    def test_allowlist_contains_only_strings(self) -> None:
        for entry in READ_ONLY_TOOLS_ALLOWLIST:
            self.assertIsInstance(entry, str)
            self.assertTrue(entry, 'no empty strings in allowlist')

    def test_allowlist_contains_no_write_or_mutating_tools(self) -> None:
        # Defense in depth: even though the doc says "read-only", an
        # accidental ``Edit`` or ``Write`` slipping in would be a
        # real foot-gun. Lock this with an explicit denylist of
        # known mutating tool names.
        forbidden = {'Edit', 'Write', 'MultiEdit', 'NotebookEdit', 'WebFetch'}
        for entry in READ_ONLY_TOOLS_ALLOWLIST:
            # Match either bare name (``Edit``) or pattern form
            # (``Bash(rm:*)`` — none of these should be present).
            for bad in forbidden:
                self.assertNotIn(
                    bad, entry,
                    f'allowlist entry {entry!r} appears to be a mutating tool',
                )

    def test_allowlist_bash_entries_use_pattern_shape(self) -> None:
        # Every Bash entry should be of shape ``Bash(<cmd>:*)``.
        # Catches a typo like ``Bash(grep)`` that would silently
        # match nothing under Claude Code's permission matcher.
        for entry in READ_ONLY_TOOLS_ALLOWLIST:
            if entry.startswith('Bash('):
                self.assertTrue(
                    entry.endswith(':*)'),
                    f'Bash entry {entry!r} must end with :*) — pattern-shape',
                )


if __name__ == '__main__':
    unittest.main()
