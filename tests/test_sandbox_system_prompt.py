"""Unit tests for ``kato.sandbox.system_prompt``.

Locks the four-state composition contract:

  * docker off, no architecture doc -> ``''``
  * docker off, architecture doc    -> the architecture doc verbatim
  * docker on,  no architecture doc -> the addendum verbatim
  * docker on,  architecture doc    -> arch + blank line + addendum

Plus a content lock on each load-bearing claim in the addendum so a
silent reword can't happen without the test failing first. The wording
was deliberately negotiated and the user signed off on it; treat
changes as a content review, not an implementation detail.
"""

from __future__ import annotations

import unittest

from kato.sandbox.system_prompt import (
    SANDBOX_SYSTEM_PROMPT_ADDENDUM,
    compose_system_prompt,
)


class ComposeSystemPromptTests(unittest.TestCase):
    def test_off_with_no_arch_doc_returns_empty(self) -> None:
        self.assertEqual(
            compose_system_prompt('', docker_mode_on=False),
            '',
        )

    def test_off_with_arch_doc_returns_arch_verbatim(self) -> None:
        arch = 'Architecture: services A, B, C with kafka in between.'
        self.assertEqual(
            compose_system_prompt(arch, docker_mode_on=False),
            arch,
        )

    def test_on_with_no_arch_doc_returns_addendum_verbatim(self) -> None:
        self.assertEqual(
            compose_system_prompt('', docker_mode_on=True),
            SANDBOX_SYSTEM_PROMPT_ADDENDUM,
        )

    def test_on_with_arch_doc_joins_with_blank_line(self) -> None:
        arch = 'Architecture: services A, B, C with kafka in between.'
        result = compose_system_prompt(arch, docker_mode_on=True)
        # Architecture comes first so the operator-authored content is
        # not buried below boilerplate.
        self.assertTrue(result.startswith(arch))
        self.assertTrue(result.endswith(SANDBOX_SYSTEM_PROMPT_ADDENDUM))
        # Joined with one blank line separating the two sections.
        self.assertIn(f'{arch}\n\n{SANDBOX_SYSTEM_PROMPT_ADDENDUM}', result)

    def test_none_arch_doc_treated_as_empty(self) -> None:
        # ``read_architecture_doc`` may return ``''`` or ``None`` —
        # the composer must accept both without raising.
        self.assertEqual(
            compose_system_prompt(None, docker_mode_on=False),  # type: ignore[arg-type]
            '',
        )
        self.assertEqual(
            compose_system_prompt(None, docker_mode_on=True),  # type: ignore[arg-type]
            SANDBOX_SYSTEM_PROMPT_ADDENDUM,
        )


class AddendumWordingLockTests(unittest.TestCase):
    """Each load-bearing claim was deliberately negotiated.

    A silent reword (e.g. softening "will not help" back to "may not
    succeed") changes the agent's behavior in production. Treat any
    failure here as a content review, not a string nit.
    """

    def test_filesystem_section_present(self) -> None:
        self.assertIn(
            "Your working directory is the operator's per-task",
            SANDBOX_SYSTEM_PROMPT_ADDENDUM,
        )
        # Concrete examples make the claim falsifiable to the agent.
        self.assertIn('/Users/...', SANDBOX_SYSTEM_PROMPT_ADDENDUM)
        self.assertIn('/home/...', SANDBOX_SYSTEM_PROMPT_ADDENDUM)
        self.assertIn('~/.config', SANDBOX_SYSTEM_PROMPT_ADDENDUM)

    def test_network_section_names_the_only_allowed_endpoint(self) -> None:
        self.assertIn('api.anthropic.com', SANDBOX_SYSTEM_PROMPT_ADDENDUM)
        # The "do not retry" framing is the load-bearing piece — without
        # it the agent reads "fail with a connection error" as a
        # transient problem and loops.
        self.assertIn(
            'These are not transient errors.',
            SANDBOX_SYSTEM_PROMPT_ADDENDUM,
        )
        self.assertIn(
            'Retrying them',
            SANDBOX_SYSTEM_PROMPT_ADDENDUM,
        )
        self.assertIn(
            'will not help.',
            SANDBOX_SYSTEM_PROMPT_ADDENDUM,
        )

    def test_network_section_names_the_failing_install_commands(self) -> None:
        # If a future agent learns a new package manager, expand this
        # list rather than dropping the existing entries.
        for command in ('apt-get', 'npm install', 'pip install', 'curl', 'wget'):
            self.assertIn(command, SANDBOX_SYSTEM_PROMPT_ADDENDUM)

    def test_privileges_section_names_sudo(self) -> None:
        self.assertIn('non-root user', SANDBOX_SYSTEM_PROMPT_ADDENDUM)
        self.assertIn('sudo is unavailable', SANDBOX_SYSTEM_PROMPT_ADDENDUM)

    def test_closing_instruction_directs_agent_to_surface_constraints(self) -> None:
        # The "surface to the operator" instruction is what stops the
        # creative-workaround failure mode.
        self.assertIn(
            'surface that to the operator in your reply',
            SANDBOX_SYSTEM_PROMPT_ADDENDUM,
        )
        self.assertIn(
            'Do not work around it by',
            SANDBOX_SYSTEM_PROMPT_ADDENDUM,
        )
        self.assertIn(
            'attempting installs or fetches that will fail.',
            SANDBOX_SYSTEM_PROMPT_ADDENDUM,
        )

    def test_addendum_does_not_mention_bypass(self) -> None:
        # Per design: the addendum describes the environment, not the
        # permission layer. Bypass mode and docker mode are independent.
        self.assertNotIn('bypass', SANDBOX_SYSTEM_PROMPT_ADDENDUM.lower())

    def test_addendum_does_not_leak_implementation_jargon(self) -> None:
        # The agent doesn't think in "bind mount" / "rootfs" / "gVisor"
        # terms; those belong in operator-facing docs.
        for jargon in ('bind-mount', 'bind mount', 'rootfs', 'gVisor', 'cgroup'):
            self.assertNotIn(jargon, SANDBOX_SYSTEM_PROMPT_ADDENDUM)


if __name__ == '__main__':
    unittest.main()
