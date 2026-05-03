"""Drift guards: BYPASS_PROTECTIONS.md "Closed" claims vs. live wiring.

The "Named open gaps" table in ``BYPASS_PROTECTIONS.md`` marks each
gap as **Open** or **Closed**. A gap is **Closed** only when:

  1. A module in ``kato_core_lib.sandbox.*`` implements the
     mitigation, AND
  2. A *production* call site in ``kato_core_lib.*`` actually
     invokes it on the relevant code path.

Without (2), the doc is lying — the module exists in the source
tree but nothing in the running system reaches it. A unit test
of the module passes, the doc says "Closed", and the operator
believes a residual is mitigated when it is not.

These tests lock the wiring. If a future refactor removes the
production call site (or renames a symbol so the call no longer
resolves), the test fails with a message that points at both the
doc claim and the missing wiring — so the operator notices BEFORE
the next ``BYPASS_PROTECTIONS.md`` update propagates a false claim.

Three gaps are guarded here, one per closed OG:

  * **OG2** — external audit-log shipping is invoked from
    ``manager.record_spawn`` after every successful local audit
    write.
  * **OG4** — TLS pin validation is invoked from ``main.main``
    before any Claude spawn.
  * **OG9a** — workspace delimiter framing is invoked from every
    prompt builder in ``cli_client.ClaudeCliClient``.

If a fourth gap closes later, add a guard test here in the same
shape. If a closure regresses (the doc still says "Closed" but
the wiring was removed), update the doc to either ``**Open**`` or
the new ``**Module ready, integration pending**`` state — the
test failure message names the right edits.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from kato_core_lib import main as kato_main
from kato_core_lib.client.claude import cli_client as cli_client_module
from kato_core_lib.sandbox import manager as sandbox_manager


_DOC_PATH = Path(__file__).resolve().parent.parent / 'BYPASS_PROTECTIONS.md'


def _doc_status_for(og_id: str) -> str:
    """Return the ``Status`` cell text for ``og_id`` from the gaps table.

    The table row format is::

        | OG2 | name ... | rationale ... | Status text ... |

    We split on ``|`` and pull the trailing column. Returns the
    empty string if the row is missing — caller can assert on the
    string content for actionable diagnostics.
    """
    text = _DOC_PATH.read_text(encoding='utf-8')
    # Match the row at the start of a line so we don't pick up
    # mentions of the OG id from prose.
    pattern = re.compile(rf'^\|\s*{re.escape(og_id)}\s*\|(.+?)$', re.MULTILINE)
    match = pattern.search(text)
    if not match:
        return ''
    columns = [col.strip() for col in match.group(1).split('|') if col.strip()]
    # The trailing column is the Status; strip any trailing ``|``
    # the regex already excluded.
    return columns[-1] if columns else ''


class OG2AuditShippingWiringTests(unittest.TestCase):
    """OG2: ``record_spawn`` must reference ``ship_audit_entry``.

    The deferred import inside ``record_spawn`` is intentional
    (keeps the manager module-load fast); we assert against the
    function's source text so a removal of the import shows up
    here even if the rest of ``record_spawn`` keeps compiling.
    """

    def test_doc_marks_og2_closed(self) -> None:
        # Sanity check: the doc claim we are guarding still exists.
        # If someone flips OG2 back to Open, the wiring assertions
        # below become unnecessary — but the test will keep failing
        # informatively until the doc is updated in matching shape.
        status = _doc_status_for('OG2')
        self.assertIn('Closed', status, (
            'BYPASS_PROTECTIONS.md OG2 row no longer says Closed; '
            'this drift-guard test should be removed or updated to '
            'match the new status.'
        ))

    def test_record_spawn_imports_and_calls_ship_audit_entry(self) -> None:
        import inspect

        source = inspect.getsource(sandbox_manager.record_spawn)
        # Deferred import line is the canonical wiring signal.
        self.assertIn(
            'from kato_core_lib.sandbox.audit_log_shipping import',
            source,
            'record_spawn no longer imports audit_log_shipping — '
            'OG2 closure is broken; either restore the import or '
            'flip OG2 to Open in BYPASS_PROTECTIONS.md.',
        )
        self.assertIn(
            'ship_audit_entry(',
            source,
            'record_spawn no longer calls ship_audit_entry() — '
            'OG2 closure is broken.',
        )
        # And it must promote required-mode failures to SandboxError
        # (otherwise "fail-closed" is decorative).
        self.assertIn('AuditShipError', source)
        self.assertIn('SandboxError', source)


class OG4TlsPinWiringTests(unittest.TestCase):
    """OG4: ``main()`` must invoke ``validate_anthropic_tls_pin_or_refuse``."""

    def test_doc_marks_og4_closed(self) -> None:
        status = _doc_status_for('OG4')
        self.assertIn('Closed', status, (
            'BYPASS_PROTECTIONS.md OG4 row no longer says Closed; '
            'this drift-guard test should be removed or updated.'
        ))

    def test_main_module_imports_tls_pin_validator(self) -> None:
        # Module-level import is what we assert: a deferred import
        # would not be wrong, but the current wiring is module-level
        # and we want to lock the shape so a refactor can't silently
        # convert it to a no-op (e.g. by importing but not calling).
        self.assertTrue(
            hasattr(kato_main, 'validate_anthropic_tls_pin_or_refuse'),
            'kato_core_lib.main no longer imports '
            'validate_anthropic_tls_pin_or_refuse — OG4 closure is '
            'broken; either restore the import or flip OG4 to Open '
            'in BYPASS_PROTECTIONS.md.',
        )
        self.assertTrue(
            hasattr(kato_main, 'TlsPinError'),
            'kato_core_lib.main no longer imports TlsPinError — '
            'main() cannot react to pin validation failures.',
        )

    def test_main_function_calls_tls_pin_validator(self) -> None:
        import inspect

        source = inspect.getsource(kato_main.main)
        self.assertIn(
            'validate_anthropic_tls_pin_or_refuse(',
            source,
            'main() no longer calls validate_anthropic_tls_pin_or_refuse() — '
            'OG4 closure is broken; the validator is imported but '
            'never invoked.',
        )
        # And it must return on TlsPinError so the failure surfaces
        # to the operator instead of being swallowed.
        self.assertIn('TlsPinError', source)


class OG9aWorkspaceDelimiterWiringTests(unittest.TestCase):
    """OG9a: every Claude prompt builder must wrap untrusted content."""

    def test_doc_marks_og9a_closed(self) -> None:
        status = _doc_status_for('OG9a')
        self.assertIn('Closed', status, (
            'BYPASS_PROTECTIONS.md OG9a row no longer says Closed; '
            'this drift-guard test should be removed or updated.'
        ))

    def test_cli_client_imports_wrap_function(self) -> None:
        self.assertTrue(
            hasattr(cli_client_module, 'wrap_untrusted_workspace_content'),
            'cli_client no longer imports wrap_untrusted_workspace_content — '
            'OG9a closure is broken; either restore the import or flip '
            'OG9a to Open in BYPASS_PROTECTIONS.md.',
        )

    def test_each_prompt_builder_calls_wrap_function(self) -> None:
        import inspect

        # The three call sites enumerated in the OG9a closure plan.
        # If a fourth prompt builder is added that handles untrusted
        # content (e.g. issue-comment expansion), add it here.
        builders = {
            '_build_implementation_prompt':
                cli_client_module.ClaudeCliClient._build_implementation_prompt,
            '_build_testing_prompt':
                cli_client_module.ClaudeCliClient._build_testing_prompt,
            '_build_review_prompt':
                cli_client_module.ClaudeCliClient._build_review_prompt,
        }
        missing: list[str] = []
        for name, fn in builders.items():
            source = inspect.getsource(fn)
            if 'wrap_untrusted_workspace_content(' not in source:
                missing.append(name)
        self.assertEqual(
            missing, [],
            f'OG9a wiring missing in: {missing}. Each Claude prompt builder '
            'must wrap externally-sourced text (task.summary/description, '
            'comment.body) via wrap_untrusted_workspace_content. Either '
            'restore the wrap call or flip OG9a to Open in '
            'BYPASS_PROTECTIONS.md.',
        )

    def test_addendum_describes_the_marker(self) -> None:
        # Independent of the wrap calls: the model needs the
        # decoder. If the addendum stops describing the marker,
        # OG9a is decorative — the model sees the tags but has no
        # rule to treat in-tag content as data.
        from kato_core_lib.sandbox.system_prompt import (
            SANDBOX_SYSTEM_PROMPT_ADDENDUM,
        )
        self.assertIn('UNTRUSTED_WORKSPACE_FILE', SANDBOX_SYSTEM_PROMPT_ADDENDUM)


if __name__ == '__main__':
    unittest.main()
