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
from agent_core_lib.agent_core_lib.helpers.comment_prompt import (
    build_comment_prompt_context,
)

# The sentence that opens the guardrail. Any module holding this as a literal
# is carrying its own copy instead of calling the helper.
LITERAL_MARKER = 'Make the smallest possible change needed'

# The two supported ways a prompt module can reach the guardrail: call the
# helper directly, or go through the comment-prompt interface (which always
# populates ``guardrails``). Derived from the real callables rather than
# written as literals, so renaming either one can never leave this test
# quietly asserting a name that no longer exists.
GUARDRAIL_HELPER = narrow_edit_guardrails_text.__name__
COMMENT_PROMPT_INTERFACE = build_comment_prompt_context.__name__
GUARDRAIL_ROUTES = (GUARDRAIL_HELPER, COMMENT_PROMPT_INTERFACE)

# Every module that builds an agent prompt asking for code changes.
PROMPT_MODULES = (
    # The CLI transports no longer assemble prompts at all — the shared mixin
    # does, once, for every one of them. Naming the clients here would guard
    # modules that build nothing while the real builder went unchecked.
    'agent_core_lib.agent_core_lib.cli_agent_shared',
    'openhands_core_lib.openhands_core_lib.openhands_client',
    # Was ``agent_service``, then ``task_comment_service``: the comment-run
    # prompt followed the run engine out of both. This list must name
    # wherever prompts are actually assembled, or the parity check silently
    # guards nothing.
    'kato_core_lib.data_layers.service.task_comment_run_service',
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
                if GUARDRAIL_HELPER not in line:
                    offenders.append(f'{dotted}: {line.strip()[:80]}')
        self.assertEqual(
            offenders, [],
            'These modules carry their own copy of the narrow-edit guardrail '
            f'instead of calling {GUARDRAIL_HELPER}(). Editing the '
            'shared helper would silently leave them behind — which is how the '
            '"agent rewrote the whole file" guardrail came to be missing from '
            'one builder in the first place.\n' + '\n'.join(offenders),
        )

    def test_every_prompt_module_actually_reaches_the_guardrail(self) -> None:
        # The mirror of the test above: absence of a literal is only good news
        # if the module still REACHES the guardrail. Two legitimate routes —
        # calling the helper directly, or going through the comment-prompt
        # interface, which always populates ``guardrails``.
        missing = [
            dotted for dotted in PROMPT_MODULES
            if not any(
                route in _module_source(dotted) for route in GUARDRAIL_ROUTES
            )
        ]
        self.assertEqual(
            missing, [],
            'These prompt modules reach the narrow-edit guardrail by neither '
            'route — the narrowing instruction is simply absent: ' + str(missing),
        )


if __name__ == '__main__':
    unittest.main()
