"""The comment-prompt interface: one way to describe a comment to an agent.

``build_comment_prompt_context`` exists so a comment surface CANNOT silently
ship a prompt with a piece missing. Every piece it returns corresponds to a
real reported failure:

    location    a prompt without it made the agent guess which file
    code        a prompt without it turned "revert this" into a file rewrite
    thread      a prompt whose thread kept the bot's own replies fed them back
    guardrails  a prompt without them had nothing saying "stay narrow"

Before the interface these were assembled by hand in each builder, and every
drift between the copies produced one of the bugs above. So these tests pin
the CONTRACT — all four parts always present, framing always applied when a
wrapper is supplied — rather than any single builder's wording.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from types import SimpleNamespace

from agent_core_lib.agent_core_lib.helpers.comment_prompt import (
    CommentPromptContext,
    CommentThreadSpec,
    build_comment_prompt_context,
)
from sandbox_core_lib.sandbox_core_lib.workspace_delimiter import (
    wrap_untrusted_workspace_content,
)

FRAME = wrap_untrusted_workspace_content('x', source_path='p').split('\n')[0][:24]


def _provider_comment(**over):
    """A provider review comment (uses ``line_number``)."""
    base = dict(file_path='auth.py', line_number=5, line_type='', commit_sha='')
    base.update(over)
    return SimpleNamespace(**base)


def _stored_comment(**over):
    """An in-app comment-store record (uses ``line``)."""
    base = dict(file_path='auth.py', line=5)
    base.update(over)
    return SimpleNamespace(**base)


class ContractTests(unittest.TestCase):
    """All four parts exist on every result — that is the whole point."""

    def test_result_always_carries_all_four_parts(self) -> None:
        ctx = build_comment_prompt_context(_provider_comment())
        for part in ('location', 'code', 'thread', 'guardrails'):
            self.assertTrue(hasattr(ctx, part), part)

    def test_guardrails_are_present_even_with_no_file_and_no_workspace(self) -> None:
        # The narrowing instruction must never depend on anything optional —
        # a builder that localizes nothing still must not over-scope.
        ctx = build_comment_prompt_context(SimpleNamespace())
        self.assertIn('Make the smallest possible change', ctx.guardrails)

    def test_context_is_immutable(self) -> None:
        ctx = build_comment_prompt_context(_provider_comment())
        with self.assertRaises(Exception):
            ctx.location = 'tampered'

    def test_as_block_orders_the_parts_and_skips_blanks(self) -> None:
        ctx = CommentPromptContext(
            location='L\n', code='', thread='T\n', guardrails='G\n',
        )
        self.assertEqual(ctx.as_block(), 'L\nT\nG\n')


class BothRecordShapesTests(unittest.TestCase):
    """Provider comments and stored comments must localize identically."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        with open(os.path.join(self._tmp.name, 'auth.py'), 'w', encoding='utf-8') as fh:
            fh.write('\n'.join(f'line {n}' for n in range(1, 11)))

    def test_line_number_and_line_produce_the_same_payload(self) -> None:
        a = build_comment_prompt_context(
            _provider_comment(), workspace_path=self._tmp.name,
        )
        b = build_comment_prompt_context(
            _stored_comment(), workspace_path=self._tmp.name,
        )
        self.assertEqual(a.location, b.location)
        self.assertEqual(a.code, b.code)
        self.assertIn('auth.py:5', a.location)
        self.assertIn('line 5', a.code)

    def test_file_level_comment_localizes_without_a_line(self) -> None:
        ctx = build_comment_prompt_context(
            _stored_comment(line=-1), workspace_path=self._tmp.name,
        )
        self.assertIn('auth.py', ctx.location)
        self.assertNotIn(':5', ctx.location)
        self.assertEqual(ctx.code, '')

    def test_missing_location_label_is_used_only_with_no_file(self) -> None:
        with_file = build_comment_prompt_context(
            _stored_comment(), missing_location_label='(none)',
        )
        without = build_comment_prompt_context(
            _stored_comment(file_path=''), missing_location_label='(none)',
        )
        self.assertNotIn('(none)', with_file.location)
        self.assertIn('(none)', without.location)


class FramingTests(unittest.TestCase):
    """A supplied wrapper must frame BOTH untrusted parts."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        with open(os.path.join(self._tmp.name, 'auth.py'), 'w', encoding='utf-8') as fh:
            fh.write('\n'.join(f'line {n}' for n in range(1, 11)))

    def _ctx(self, **over):
        params = dict(
            workspace_path=self._tmp.name,
            wrap=wrap_untrusted_workspace_content,
            thread=CommentThreadSpec(
                entries=({'author': 'dave', 'body': 'do it differently'},),
                header='\n\nThread:\n',
            ),
        )
        params.update(over)
        return build_comment_prompt_context(_provider_comment(), **params)

    def test_code_and_thread_are_both_framed(self) -> None:
        ctx = self._ctx()
        self.assertIn(FRAME, ctx.code)
        self.assertIn(FRAME, ctx.thread)

    def test_omitting_the_wrapper_leaves_both_unframed(self) -> None:
        # Explicit, so the danger of omitting it is visible in the contract.
        ctx = self._ctx(wrap=None)
        self.assertNotIn(FRAME, ctx.code)
        self.assertNotIn(FRAME, ctx.thread)

    def test_thread_source_label_is_per_surface(self) -> None:
        ctx = self._ctx(thread=CommentThreadSpec(
            entries=({'author': 'dave', 'body': 'x'},),
            header='\n\nThread:\n',
            source_path='pr-comment-thread',
        ))
        self.assertIn('pr-comment-thread', ctx.thread)


class ThreadRulesTests(unittest.TestCase):
    """Self-reply filtering and the no-orphan-header rule."""

    def test_bot_self_replies_are_dropped(self) -> None:
        ctx = build_comment_prompt_context(
            _provider_comment(),
            thread=CommentThreadSpec(
                entries=(
                    {'author': 'dave', 'body': 'please fix'},
                    {'author': 'bot', 'body': 'BOT-REPLY: addressed it'},
                ),
                header='\n\nThread:\n',
                drop_prefixes=('BOT-REPLY:',),
            ),
        )
        self.assertIn('please fix', ctx.thread)
        self.assertNotIn('BOT-REPLY:', ctx.thread)

    def test_a_thread_of_only_self_replies_renders_nothing(self) -> None:
        ctx = build_comment_prompt_context(
            _provider_comment(),
            thread=CommentThreadSpec(
                entries=({'author': 'bot', 'body': 'BOT-REPLY: addressed'},),
                header='\n\nThread:\n',
                drop_prefixes=('BOT-REPLY:',),
            ),
        )
        self.assertEqual(ctx.thread, '')

    def test_no_thread_spec_means_no_thread_text(self) -> None:
        self.assertEqual(build_comment_prompt_context(_provider_comment()).thread, '')

    def test_label_for_overrides_the_author(self) -> None:
        ctx = build_comment_prompt_context(
            _provider_comment(),
            thread=CommentThreadSpec(
                entries=({'author': 'dave', 'body': 'x'},),
                header='\n\nThread:\n',
                label_for=lambda _: 'Operator',
            ),
        )
        self.assertIn('Operator: x', ctx.thread)


if __name__ == '__main__':
    unittest.main()
