"""Every prompt that asks the agent to CHANGE code must carry the same
narrow-edit guardrail, from the same helper.

The guardrail exists because of a specific operator report: told to "revert
this" on one commented line, the agent rewrote the whole file. The three
sentences that tell it to stay narrow were originally copy-pasted into each
prompt builder, and one builder — the in-app diff-comment prompt — never got
them at all, which is how a terse comment could still be read as licence to
rewrite everything.

Copy-pasted safety text rots: editing one copy silently improves one transport
and leaves the others behind. So this file asserts STRUCTURALLY that every
transport calls ``narrow_edit_guardrails_text`` rather than carrying its own
literal. It is deliberately a source-level assertion — a text assertion would
pass right up until someone edits the shared helper and the stale copies
diverge, which is the exact failure being prevented.
"""

from __future__ import annotations

import inspect
import re
import unittest

from agent_core_lib.agent_core_lib.helpers.agent_prompt_utils import (
    narrow_edit_guardrails_text,
)

# The sentence that opens the guardrail. Any module holding this as a literal
# is carrying its own copy instead of calling the helper.
LITERAL_MARKER = 'Make the smallest possible change needed'

# Every module that builds an agent prompt asking for code changes.
PROMPT_MODULES = (
    'claude_core_lib.claude_core_lib.cli_client',
    'codex_core_lib.codex_core_lib.cli_client',
    'openhands_core_lib.openhands_core_lib.openhands_client',
    'kato_core_lib.data_layers.service.agent_service',
)


def _module_source(dotted: str) -> str:
    module = __import__(dotted, fromlist=['_'])
    return inspect.getsource(module)


class GuardrailHelperOutputTests(unittest.TestCase):
    """The helper itself must keep emitting all three sentences."""

    def test_emits_the_three_narrowing_rules(self) -> None:
        text = narrow_edit_guardrails_text('to address it')
        self.assertIn('Make the smallest possible change needed to address it.', text)
        self.assertIn('Prefer editing only the exact lines or blocks', text)
        self.assertIn('Do not change indentation, formatting, or unrelated lines', text)

    def test_bulleted_form_prefixes_every_line(self) -> None:
        lines = narrow_edit_guardrails_text('to satisfy the task', bulleted=True)
        self.assertTrue(
            all(line.startswith('- ') for line in lines.strip().split('\n')),
        )

    def test_unbulleted_form_prefixes_nothing(self) -> None:
        lines = narrow_edit_guardrails_text('to satisfy the task')
        self.assertFalse(
            any(line.startswith('- ') for line in lines.strip().split('\n')),
        )


class NoTransportCarriesItsOwnCopyTests(unittest.TestCase):
    """THE regression guard: no prompt module may hardcode the guardrail."""

    def test_no_prompt_module_hardcodes_the_guardrail(self) -> None:
        offenders = []
        for dotted in PROMPT_MODULES:
            source = _module_source(dotted)
            # A hit that is NOT inside a call to the helper means a private copy.
            for match in re.finditer(re.escape(LITERAL_MARKER), source):
                line_start = source.rfind('\n', 0, match.start()) + 1
                line = source[line_start:source.find('\n', match.start())]
                if 'narrow_edit_guardrails_text' not in line:
                    offenders.append(f'{dotted}: {line.strip()[:80]}')
        self.assertEqual(
            offenders, [],
            'These modules carry their own copy of the narrow-edit guardrail '
            'instead of calling narrow_edit_guardrails_text(). Editing the '
            'shared helper would silently leave them behind — which is how the '
            '"agent rewrote the whole file" guardrail came to be missing from '
            'one builder in the first place.\n' + '\n'.join(offenders),
        )

    def test_every_prompt_module_actually_calls_the_helper(self) -> None:
        # The mirror of the test above: absence of a literal is only good news
        # if the module calls the helper instead of dropping the guardrail.
        missing = [
            dotted for dotted in PROMPT_MODULES
            if 'narrow_edit_guardrails_text' not in _module_source(dotted)
        ]
        self.assertEqual(
            missing, [],
            'These prompt modules reference the guardrail helper nowhere at '
            'all — the narrowing instruction is simply absent: ' + str(missing),
        )


if __name__ == '__main__':
    unittest.main()
