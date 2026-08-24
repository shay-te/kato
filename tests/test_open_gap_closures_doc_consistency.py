"""Drift guards: BYPASS_PROTECTIONS.md "Closed" claims vs. live wiring.

The "Named open gaps" table in ``BYPASS_PROTECTIONS.md`` marks each
gap as **Open** or **Closed**. A gap is **Closed** only when:

  1. A module in ``sandbox_core_lib.sandbox_core_lib.*`` implements the
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
from claude_core_lib.claude_core_lib import cli_client as cli_client_module
from codex_core_lib.codex_core_lib import cli_client as codex_cli_client_module
from openhands_core_lib.openhands_core_lib import (
    openhands_client as openhands_client_module,
)
from sandbox_core_lib.sandbox_core_lib import manager as sandbox_manager


_DOC_PATH = Path(__file__).resolve().parent.parent / 'sandbox_core_lib' / 'SANDBOX_PROTECTIONS.md'


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
            'from sandbox_core_lib.sandbox_core_lib.audit_log_shipping import',
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

    def test_doc_describes_tofu_lifecycle(self) -> None:
        # The OG4 row was rewritten when the lifecycle moved from
        # strict-by-default to TOFU. Lock that the doc still names
        # the load-bearing pieces — without these, the row reads
        # like the old strict-only description and operators can't
        # tell which behaviour they should expect.
        doc_text = _DOC_PATH.read_text(encoding='utf-8')
        for phrase in (
            'TOFU',
            'KATO_SANDBOX_ANTHROPIC_TLS_PIN_SHA256',
            'KATO_SANDBOX_ALLOW_NO_TLS_PIN',
            'first run',
            'subsequent run',
            'rotation',
        ):
            self.assertIn(
                phrase, doc_text,
                f'BYPASS_PROTECTIONS.md OG4 description no longer mentions '
                f'{phrase!r}. Either restore the doc text or update this '
                f'drift-guard if the lifecycle has genuinely changed.',
            )

    def test_doc_pin_file_path_matches_source_default(self) -> None:
        # The doc tells operators to ``rm ~/.kato/anthropic-tls-pin``
        # on cert rotation. If the source code changes the default
        # path without updating the doc (or vice versa), operators
        # following the doc's recovery steps would do nothing
        # (deleting a non-existent file) and stay broken. Lock the
        # two together.
        from sandbox_core_lib.sandbox_core_lib.tls_pin import _default_pin_file_path

        path = _default_pin_file_path()
        # Render with ~ substitution the same way the runtime does.
        try:
            home = Path.home()
            rel = path.resolve().relative_to(home.resolve())
            display = f'~/{rel}'
        except (ValueError, RuntimeError, KeyError):
            display = str(path)

        doc_text = _DOC_PATH.read_text(encoding='utf-8')
        self.assertIn(
            display, doc_text,
            f'tls_pin._default_pin_file_path() resolves to {display!r} '
            f'but BYPASS_PROTECTIONS.md does not mention this exact '
            f'path. Operators following the doc recovery steps '
            f'(``rm <path>``) would target the wrong file.',
        )


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

    def test_every_prompt_path_wraps_untrusted_content(self) -> None:
        """Every prompt that embeds externally-sourced text must frame it.

        The implementation and testing prompts are now built ONCE, on
        ``CliAgentSharedBehaviour``, so the wrap call moved with them: the
        shared builder frames the task body through the ``_wrap_untrusted``
        hook, and each transport binds that hook to the real delimiter
        function (checked separately below). The review builders are still
        per-transport and are checked directly.

        A prior version of this guard only looked at ClaudeCliClient — an
        audit then found codex's review builders missing the addendum wiring
        and openhands with ZERO wrapping anywhere. Hence: every transport,
        every path.
        """
        import inspect

        from agent_core_lib.agent_core_lib.cli_agent_shared import (
            CliAgentSharedBehaviour,
        )

        # The shared path: one builder pair for every CLI transport.
        shared_source = inspect.getsource(CliAgentSharedBehaviour._untrusted_task_body)
        self.assertIn(
            'self._wrap_untrusted(', shared_source,
            'the shared prompt scaffolding no longer frames the task body; '
            'OG9a closure is broken for EVERY CLI transport at once.',
        )
        for builder in ('_build_implementation_prompt', '_build_testing_prompt'):
            with self.subTest(builder=builder):
                source = inspect.getsource(getattr(CliAgentSharedBehaviour, builder))
                self.assertIn(
                    'self._untrusted_task_body(task)', source,
                    f'{builder} stopped routing the task body through the '
                    'framing helper.',
                )
        # The review prompt is shared too, and frames TWO untrusted inputs:
        # the reviewer's comment body, and the code inlined from the workspace
        # at the commented line (plantable by anyone with merge access).
        for builder in ('_build_review_prompt', '_build_review_comments_batch_prompt'):
            with self.subTest(builder=builder):
                source = inspect.getsource(getattr(CliAgentSharedBehaviour, builder))
                self.assertIn(
                    'cls._wrap_untrusted_text(', source,
                    f'{builder} no longer frames the reviewer comment body.',
                )
                self.assertIn(
                    'wrap=cls._wrap_untrusted_text', source,
                    f'{builder} no longer frames the inlined code context — a '
                    'poisoned comment near the reviewed line would ride in as '
                    'trusted text. The batch path inlines the MOST content.',
                )

        # The per-transport paths: review prompts, and openhands (an API
        # client, not a CLI one — it does not share the mixin).
        builders = {
            'openhands._build_implementation_prompt':
                openhands_client_module.OpenHandsClient._build_implementation_prompt,
            'openhands._build_testing_prompt':
                openhands_client_module.OpenHandsClient._build_testing_prompt,
            'openhands._build_review_prompt':
                openhands_client_module.OpenHandsClient._build_review_prompt,
            'openhands._build_review_comments_batch_prompt':
                openhands_client_module.OpenHandsClient._build_review_comments_batch_prompt,
        }
        missing = [
            name for name, fn in builders.items()
            if 'wrap_untrusted_workspace_content(' not in inspect.getsource(fn)
        ]
        self.assertEqual(
            missing, [],
            f'OG9a wiring missing in: {missing}. Each prompt builder that '
            'embeds externally-sourced text (task.summary/description, '
            'comment.body) must wrap it via wrap_untrusted_workspace_content. '
            'Either restore the wrap call or flip OG9a to Open in '
            'BYPASS_PROTECTIONS.md.',
        )

    def test_every_cli_transport_binds_the_framing_hook(self) -> None:
        """``_wrap_untrusted`` must reach the real delimiter, in every transport.

        The shared builders frame through this hook, so a transport that
        bound it to anything else — or to identity — would send unframed
        issue text while every other check still passed.
        """
        import inspect

        for label, client in (
            ('claude', cli_client_module.ClaudeCliClient),
            ('codex', codex_cli_client_module.CodexCliClient),
        ):
            with self.subTest(transport=label):
                for hook in ('_wrap_untrusted', '_wrap_untrusted_text'):
                    source = inspect.getsource(getattr(client, hook))
                    self.assertIn(
                        'wrap_untrusted_workspace_content(', source,
                        f'{label}.{hook} no longer binds to the real '
                        'delimiter function — OG9a closure is broken for it.',
                    )

    def test_addendum_describes_the_marker(self) -> None:
        # Independent of the wrap calls: the model needs the
        # decoder. If the addendum stops describing the marker,
        # OG9a is decorative — the model sees the tags but has no
        # rule to treat in-tag content as data. Checked on the
        # ALWAYS-ON addendum, not the docker-only sandbox one — every
        # transport wraps content unconditionally, so the explanation
        # must be unconditional too (a prior version of this guard
        # checked the docker-only addendum, which would have missed
        # the addendum going silently inert outside docker mode).
        from sandbox_core_lib.sandbox_core_lib.system_prompt import (
            UNTRUSTED_WORKSPACE_CONTENT_ADDENDUM,
        )
        self.assertIn('UNTRUSTED_WORKSPACE_FILE', UNTRUSTED_WORKSPACE_CONTENT_ADDENDUM)


class ReadOnlyToolsAllowlistPinTests(unittest.TestCase):
    """Drift guard: pin the exact membership of ``READ_ONLY_TOOLS_ALLOWLIST``.

    The allowlist controls which Bash commands are pre-approved
    when ``KATO_CLAUDE_ALLOWED_READ_ONLY_TOOLS=true``. Widening it
    is a security decision (an operator can pick a command they
    *think* is read-only but isn't — ``find -delete``, ``sed -i``,
    ``tee``, ``dd``, ``curl > file``). This test pins the exact
    set so adding an entry requires changing both the constant
    and the test, which forces a code review on the widening.

    The test is in this file (not in
    ``test_read_only_tools_validator.py``) because it has the same
    semantics as the other drift-guards: the doc says "X is the
    allowlist", and this test makes the doc claim load-bearing.

    To widen the allowlist:
      1. Add the entry to
         ``sandbox_core_lib.sandbox_core_lib.bypass_permissions_validator.READ_ONLY_TOOLS_ALLOWLIST``.
      2. Add the same entry to ``_PINNED_READ_ONLY_ALLOWLIST`` below.
      3. Update the BYPASS_PROTECTIONS.md TL;DR + Recent changes
         entry that enumerates the allowlist members.
      4. Get a security review on the additions before merging.
    """

    # Keep this list in lock-step with
    # ``sandbox_core_lib.sandbox_core_lib.bypass_permissions_validator.READ_ONLY_TOOLS_ALLOWLIST``.
    # Sorted so diff churn on additions is minimal.
    _PINNED_READ_ONLY_ALLOWLIST = frozenset({
        'Bash(cat:*)',
        'Bash(file:*)',
        'Bash(find:*)',
        'Bash(grep:*)',
        'Bash(head:*)',
        'Bash(ls:*)',
        'Bash(rg:*)',
        'Bash(stat:*)',
        'Bash(tail:*)',
        'Bash(wc:*)',
        'Read',
    })

    def test_allowlist_membership_matches_pin(self) -> None:
        from sandbox_core_lib.sandbox_core_lib.bypass_permissions_validator import (
            READ_ONLY_TOOLS_ALLOWLIST,
        )
        self.assertEqual(
            set(READ_ONLY_TOOLS_ALLOWLIST),
            set(self._PINNED_READ_ONLY_ALLOWLIST),
            'READ_ONLY_TOOLS_ALLOWLIST membership has drifted from the '
            'pinned set in this test. Widening the allowlist is a '
            'security decision — see the class docstring for the '
            'required process (constant + this pin + doc + review).',
        )

    def test_doc_tldr_lists_each_allowlisted_command(self) -> None:
        # The TL;DR enumerates the allowlist members so operators
        # don't have to read source to know what's pre-approved.
        # Lock that the doc claim matches the allowlist.
        doc_text = _DOC_PATH.read_text(encoding='utf-8')
        for entry in self._PINNED_READ_ONLY_ALLOWLIST:
            # ``Bash(grep:*)`` -> ``grep`` for the doc check; the
            # TL;DR uses bare command names with backticks.
            if entry.startswith('Bash(') and entry.endswith(':*)'):
                bare = entry[len('Bash('):-len(':*)')]
                self.assertIn(
                    f'`{bare}`', doc_text,
                    f'BYPASS_PROTECTIONS.md TL;DR no longer mentions '
                    f'pre-approved command {bare!r}. Update the doc to '
                    f'match the allowlist or update the allowlist to '
                    f'match the doc.',
                )
            else:
                # ``Read`` is named verbatim in the doc.
                self.assertIn(
                    f'`{entry}`', doc_text,
                    f'BYPASS_PROTECTIONS.md TL;DR no longer mentions '
                    f'pre-approved tool {entry!r}.',
                )

    def test_cli_client_emits_pinned_allowlist_in_argv(self) -> None:
        # End-to-end: when the read-only flag is on, the argv built
        # by ``ClaudeCliClient._build_command`` contains every entry
        # in the pinned allowlist. Locks the wiring from constant to
        # subprocess invocation.
        from claude_core_lib.claude_core_lib.cli_client import ClaudeCliClient

        client = ClaudeCliClient(binary='claude', read_only_tools_on=True)
        cmd = client._build_command(additional_dirs=[], agent_session_id='')
        idx = cmd.index('--allowedTools')
        argv_value = cmd[idx + 1]
        for entry in self._PINNED_READ_ONLY_ALLOWLIST:
            self.assertIn(
                entry, argv_value,
                f'pinned allowlist entry {entry!r} not present in '
                f'--allowedTools argv: {argv_value}',
            )


if __name__ == '__main__':
    unittest.main()
