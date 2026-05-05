"""Content-lock tests for the recent BYPASS_PROTECTIONS.md changes.

The existing drift-guard (``test_bypass_protections_doc_consistency.py``)
locks anchor-block invariants — set-equality between doc bullets
and ``manager.py`` constants. That mechanism doesn't cover **prose**
sections of the doc, so this file picks up the slack for the prose
edits that landed alongside the new defenses (mandatory base-image
digest pin + mandatory Claude CLI version pin):

  1. **Risk #S2 / #S5** in the legacy "Supply chain" table — both
     must be **M** (Mitigated), not **B** (Bounded), and the
     rationale must name the new mandatory pin.
  2. **Second-tier hardening section** must list the new
     "Mandatory Claude CLI version pin" bullet alongside the
     existing "Mandatory base-image digest pin".
  3. **Residual model "Build-time supply chain" subsection** must
     reflect the new mandatory-by-default policy — not the old
     "accepted, with operator-discretionary mitigation" framing.
  4. **Attack-and-defense map row #17** must say BOTH pins are
     mandatory and BOTH opt-out env vars are named.

These are prose-level locks: a future reword that softens the
status (e.g. "operator should consider pinning") or drops the
opt-out env var name will fail this suite before it ships. Same
discipline as the system-prompt addendum wording locks.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


_DOC_PATH = (
    Path(__file__).resolve().parent.parent.parent / 'SANDBOX_PROTECTIONS.md'
)


def _doc_text() -> str:
    """Read the doc once per test (the file is small; no caching needed)."""
    return _DOC_PATH.read_text(encoding='utf-8')


def _section_text(start_marker: str, end_marker: str) -> str:
    """Return the slice of the doc between two anchor strings.

    Both markers must appear in the doc. The slice does NOT include
    either marker — caller asserts on the body.
    """
    text = _doc_text()
    start = text.find(start_marker)
    if start < 0:
        raise AssertionError(
            f'start marker {start_marker!r} not found in {_DOC_PATH}'
        )
    end = text.find(end_marker, start + len(start_marker))
    if end < 0:
        raise AssertionError(
            f'end marker {end_marker!r} not found after {start_marker!r} '
            f'in {_DOC_PATH}'
        )
    return text[start + len(start_marker):end]


def _table_row(row_id: str) -> str:
    """Return a single-line table row that begins with ``| <row_id> |``.

    Tests use this to locate a specific row in a multi-row markdown
    table without depending on the table's surrounding context.
    """
    pattern = re.compile(rf'^\|\s*{re.escape(row_id)}\s*\|.*$', re.MULTILINE)
    for match in pattern.finditer(_doc_text()):
        return match.group(0)
    raise AssertionError(
        f'no markdown table row found with id {row_id!r} in {_DOC_PATH}'
    )


class S2RowLockTests(unittest.TestCase):
    """Risk #S2: ``npm install`` pulls a tampered package.

    Was **B** (Bounded — operator-discretionary pin). Is now **M**
    (Mitigated — mandatory pin). Rationale must name the new env
    vars so a doc reader can find them without searching elsewhere.
    """

    def setUp(self) -> None:
        self.row = _table_row('S2')

    def test_s2_status_is_mitigated(self) -> None:
        """``**M**`` is the load-bearing token. ``**B**`` would mean the
        legacy table contradicts the policy update."""
        self.assertIn('| **M** |', self.row)
        self.assertNotIn('| **B** |', self.row)

    def test_s2_names_the_mandatory_cli_version_env_var(self) -> None:
        self.assertIn('KATO_SANDBOX_CLAUDE_CLI_VERSION', self.row)

    def test_s2_names_the_opt_out_env_var(self) -> None:
        # The opt-out is named so an operator who needs the residual
        # has the exact knob without searching elsewhere.
        self.assertIn('KATO_SANDBOX_ALLOW_FLOATING_CLAUDE_CLI', self.row)

    def test_s2_uses_strict_default_framing(self) -> None:
        # "Mandatory" / "refuses to build" framing — anything softer
        # ("operator can pin", "recommended") would be a regression.
        self.assertIn('Mandatory', self.row)
        self.assertIn('refuses to build', self.row)


class S5RowLockTests(unittest.TestCase):
    """Risk #S5: image rebuild fetches a different ``latest`` Claude.

    Was **B** (operator-discretionary). Is now **M** (mandatory pin
    forces the same behavior across rebuilds).
    """

    def setUp(self) -> None:
        self.row = _table_row('S5')

    def test_s5_status_is_mitigated(self) -> None:
        self.assertIn('| **M** |', self.row)
        self.assertNotIn('| **B** |', self.row)

    def test_s5_names_the_mandatory_cli_version_env_var(self) -> None:
        self.assertIn('KATO_SANDBOX_CLAUDE_CLI_VERSION', self.row)

    def test_s5_cross_references_s2(self) -> None:
        # S5 is the same defense as S2 from a different angle —
        # cross-reference makes that explicit so the reader doesn't
        # think they're independent mitigations.
        self.assertIn('S2', self.row)


class SecondTierHardeningCliVersionPinLockTests(unittest.TestCase):
    """The new "Mandatory Claude CLI version pin" bullet must exist.

    The Second-tier hardening section is the canonical inventory of
    strict-by-default protections. A new strict-by-default protection
    that doesn't appear here means the operator has to read the
    Recent-changes section to discover it — exactly the doc-vs-code
    drift this file exists to prevent.
    """

    def setUp(self) -> None:
        self.section = _section_text(
            '## Second-tier hardening',
            '## Operational hardening',
        )

    def test_section_lists_mandatory_cli_version_pin(self) -> None:
        self.assertIn('Mandatory Claude CLI version pin', self.section)

    def test_bullet_marked_strict_by_default(self) -> None:
        # The "strict by default" framing matches the base-image
        # entry's structure — operator scanning the section sees both
        # protections classified the same way.
        # Look for the strict-by-default annotation within the CLI
        # version pin bullet specifically.
        bullet_start = self.section.index('Mandatory Claude CLI version pin')
        # The bullet runs until the next ``- **`` marker (next bullet).
        next_bullet = self.section.find('- **', bullet_start + 1)
        if next_bullet < 0:
            bullet_text = self.section[bullet_start:]
        else:
            bullet_text = self.section[bullet_start:next_bullet]
        self.assertIn('strict by default', bullet_text.lower())

    def test_bullet_names_the_pin_env_var(self) -> None:
        self.assertIn('KATO_SANDBOX_CLAUDE_CLI_VERSION', self.section)

    def test_bullet_names_the_opt_out_env_var(self) -> None:
        self.assertIn('KATO_SANDBOX_ALLOW_FLOATING_CLAUDE_CLI', self.section)

    def test_bullet_appears_after_base_image_pin(self) -> None:
        # Operator-facing ordering: base-image first (the "headline"
        # supply-chain pin), then the npm-side pin. Reverse order
        # would be confusing because the rationale builds on the
        # base-image entry.
        base_idx = self.section.index('Mandatory base-image digest pin')
        cli_idx = self.section.index('Mandatory Claude CLI version pin')
        self.assertLess(
            base_idx, cli_idx,
            'CLI version pin bullet must come AFTER base-image pin '
            'so the rationale flow reads correctly',
        )

    def test_section_notes_both_pins_validated_before_docker(self) -> None:
        # Fail-fast property — naming it explicitly so a refactor that
        # moves the validators into the docker call would surface as
        # a doc-vs-code drift.
        self.assertIn(
            'validated before any docker invocation',
            self.section,
        )


class ResidualModelBuildTimeSupplyChainLockTests(unittest.TestCase):
    """The Residual model "Build-time supply chain" subsection rewrite.

    Was titled "accepted, with operator-discretionary mitigation".
    Is now titled "mitigated by default, open gap for full
    build-time sandbox" — a doc reader who lands on this section
    must understand the policy is mandatory-by-default, not
    optional.
    """

    def setUp(self) -> None:
        # Subsection runs from its header to the next ``###``.
        self.section = _section_text(
            '### Build-time supply chain',
            '### cgroup namespace isolation',
        )

    def test_subsection_header_reflects_new_policy(self) -> None:
        # The header itself encodes the policy. The OLD framing
        # ("accepted, with operator-discretionary mitigation") would
        # mean the doc still claims the build-time supply chain is
        # operator-discretionary — directly contradicting the
        # mandatory pins below.
        self.assertIn('mitigated by default', self.section)
        self.assertNotIn('accepted, with operator-discretionary', self.section)

    def test_subsection_lists_both_mandatory_pins(self) -> None:
        # Both pins must be named so a reader scanning the residual
        # model sees the same structure as the Second-tier
        # hardening entry — no "the doc names one but the policy
        # enforces two" mismatch.
        self.assertIn('Base image', self.section)
        self.assertIn('Claude CLI version', self.section)
        self.assertIn('KATO_SANDBOX_BASE_IMAGE', self.section)
        self.assertIn('KATO_SANDBOX_CLAUDE_CLI_VERSION', self.section)

    def test_subsection_names_both_opt_outs(self) -> None:
        # Operators reading the residual model are exactly the audience
        # that wants the override knobs. Both must be discoverable
        # without leaving the section.
        self.assertIn('KATO_SANDBOX_ALLOW_FLOATING_BASE_IMAGE', self.section)
        self.assertIn('KATO_SANDBOX_ALLOW_FLOATING_CLAUDE_CLI', self.section)

    def test_subsection_names_the_remaining_open_gap(self) -> None:
        # OG1 (full build-time sandbox) is the named open gap. Without
        # this cross-reference, a reader could mistake "mitigated by
        # default" for "fully closed."
        self.assertIn('OG1', self.section)
        # The remaining attack surface is named in operator-
        # understandable terms — apt sources / transitive npm /
        # postinstall — so the operator knows what the pins do NOT
        # cover.
        self.assertIn('postinstall', self.section)
        self.assertIn('transitive', self.section)


class AttackMapRow17LockTests(unittest.TestCase):
    """Attack-and-defense map row #17.

    The headline summary the operator scans first. Must say BOTH
    pins are mandatory; must name BOTH opt-out env vars; must
    point at OG1 as the named open gap for the remaining residual.
    """

    def setUp(self) -> None:
        self.row = _table_row('17')

    def test_row_says_mitigated_for_both_substitution_paths(self) -> None:
        # "Mitigated for base-image substitution AND npm-side
        # substitution" is the load-bearing phrase. A reword that
        # softens to "mostly mitigated" or drops one of the two
        # would be a regression.
        self.assertIn('base-image substitution', self.row)
        self.assertIn('npm-side substitution', self.row)
        self.assertIn('Mitigated', self.row)

    def test_row_names_both_pin_env_vars(self) -> None:
        self.assertIn('KATO_SANDBOX_BASE_IMAGE', self.row)
        self.assertIn('KATO_SANDBOX_CLAUDE_CLI_VERSION', self.row)

    def test_row_names_both_opt_out_env_vars(self) -> None:
        self.assertIn('KATO_SANDBOX_ALLOW_FLOATING_BASE_IMAGE', self.row)
        self.assertIn('KATO_SANDBOX_ALLOW_FLOATING_CLAUDE_CLI', self.row)

    def test_row_names_open_gap_for_full_build_sandbox(self) -> None:
        # OG1 cross-reference matches the Residual model section's
        # framing — internal consistency between the attack map and
        # the residual narrative.
        self.assertIn('OG1', self.row)


class RecentChangesEntryLockTests(unittest.TestCase):
    """The "Recent changes" section narrates what landed.

    Operators scanning Recent changes must learn about the new
    defenses without having to read the rest of the doc. Each
    landed defense from this thread must appear there.
    """

    def setUp(self) -> None:
        self.section = _section_text(
            '## Recent changes',
            '## Cross-OS support matrix',
        )

    def test_recent_changes_announces_two_flag_refactor(self) -> None:
        self.assertIn('two independent flags', self.section)
        self.assertIn('KATO_CLAUDE_DOCKER', self.section)
        self.assertIn('KATO_CLAUDE_BYPASS_PERMISSIONS', self.section)

    def test_recent_changes_announces_credential_pattern_detector(self) -> None:
        # Both surfaces (preventive + detective) named so operators
        # know the defense exists at both spawn-time and response-time.
        self.assertIn('Credential pattern detector', self.section)
        self.assertIn('preventive', self.section.lower())
        self.assertIn('detective', self.section.lower())

    def test_recent_changes_announces_phishing_detector(self) -> None:
        self.assertIn('phishing', self.section.lower())
        # Specific patterns named — abstract "phishing" alone leaves
        # the operator without a sense of what's caught.
        self.assertIn('curl ... | bash', self.section)
        self.assertIn('eval', self.section)

    def test_recent_changes_announces_mandatory_base_image_pin(self) -> None:
        self.assertIn('Mandatory base-image digest pin', self.section)
        self.assertIn('KATO_SANDBOX_BASE_IMAGE', self.section)
        self.assertIn('KATO_SANDBOX_ALLOW_FLOATING_BASE_IMAGE', self.section)


if __name__ == '__main__':
    unittest.main()
