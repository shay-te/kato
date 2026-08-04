"""Every CLI transport must build the SAME review-comment context.

The bug this exists to prevent, verbatim from the operator: kato had made
changes to a file, they commented "revert this" on a SINGLE line, and it
reverted the entire file. Cause: the prompt carried the file path and a line
NUMBER but not the code at that line, so "this" was a guess — and an
under-specified guess overshoots.

That was fixed by routing every comment-driven prompt through one builder
(``build_comment_prompt_context``). The fix landed on the Claude transport.
Codex kept hand-assembling the same four pieces and its copy read the line
from ``line_number`` ONLY — so provider PR comments still got their snippet
but IN-APP DIFF COMMENTS, which carry ``line``, silently got none. The exact
reported failure was still live on that backend, months later, with every
test passing.

So the assertions here are deliberately cross-transport: a fix that lands on
one backend and not the others fails this file.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from types import SimpleNamespace

from claude_core_lib.claude_core_lib.cli_client import ClaudeCliClient
from codex_core_lib.codex_core_lib.cli_client import CodexCliClient

#: Every CLI transport that builds review-comment prompts. Add new ones here.
TRANSPORTS = (('claude', ClaudeCliClient), ('codex', CodexCliClient))

#: The two record shapes a comment can arrive in. Provider review comments use
#: ``line_number``; the in-app diff-comment store uses ``line``. Both reach the
#: same prompt builders, so both must resolve.
LINE_FIELDS = ('line_number', 'line')


class ReviewPromptSnippetParityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = self._tmp.name
        os.makedirs(os.path.join(self.workspace, 'src'))
        with open(os.path.join(self.workspace, 'src', 'app.py'), 'w') as handle:
            handle.write('\n'.join(f'line {n}' for n in range(1, 60)))

    def _comment(self, line_field: str, **overrides):
        payload = dict(
            id='C1', author='alice', body='revert this',
            file_path='src/app.py', all_comments=[],
            repository_local_path=self.workspace,
        )
        payload[line_field] = 42
        payload.update(overrides)
        return SimpleNamespace(**payload)

    def test_every_transport_inlines_the_code_for_both_comment_shapes(self) -> None:
        for name, client in TRANSPORTS:
            for line_field in LINE_FIELDS:
                with self.subTest(transport=name, line_field=line_field):
                    prompt = client._build_review_prompt(
                        self._comment(line_field), 'feat/x',
                        workspace_path=self.workspace,
                    )
                    self.assertIn(
                        'line 42', prompt,
                        f'{name} did not inline the commented code for a '
                        f'{line_field}-shaped comment — the agent sees only a '
                        'line NUMBER and has to guess what "this" means',
                    )

    def test_every_transport_carries_the_narrow_edit_guardrail(self) -> None:
        # The snippet says WHAT was commented on; the guardrail says how far to
        # go. Losing either one reproduces the over-broad edit.
        for name, client in TRANSPORTS:
            for line_field in LINE_FIELDS:
                with self.subTest(transport=name, line_field=line_field):
                    prompt = client._build_review_prompt(
                        self._comment(line_field), 'feat/x',
                        workspace_path=self.workspace,
                    )
                    self.assertIn('to address the review comment', prompt)

    def test_every_transport_frames_the_snippet_as_untrusted(self) -> None:
        # Repo file content is plantable by anyone with merge access, so it is
        # data, not instructions.
        for name, client in TRANSPORTS:
            with self.subTest(transport=name):
                prompt = client._build_review_prompt(
                    self._comment('line_number'), 'feat/x',
                    workspace_path=self.workspace,
                )
                snippet_at = prompt.index('line 42')
                marker_at = prompt.rindex('UNTRUSTED_WORKSPACE_FILE', 0, snippet_at)
                self.assertLess(marker_at, snippet_at,
                                f'{name} emitted the code snippet unframed')

    def test_no_transport_emits_an_empty_delimiter_block(self) -> None:
        # An unreadable file must yield NO snippet block at all. One copy
        # wrapped unconditionally and emitted bare delimiter tags around
        # nothing, which is noise the agent has to interpret.
        missing = self._comment('line_number', file_path='src/does-not-exist.py')
        for name, client in TRANSPORTS:
            with self.subTest(transport=name):
                prompt = client._build_review_prompt(
                    missing, 'feat/x', workspace_path=self.workspace)
                self.assertNotIn('Code at line', prompt)

    def test_transports_agree_on_whether_context_is_present(self) -> None:
        # Not a byte-for-byte comparison — the transports legitimately differ
        # in system-prompt delivery and tool vocabulary. What must match is
        # WHICH pieces of comment context made it in.
        for line_field in LINE_FIELDS:
            with self.subTest(line_field=line_field):
                comment = self._comment(line_field)
                present = {}
                for name, client in TRANSPORTS:
                    prompt = client._build_review_prompt(
                        comment, 'feat/x', workspace_path=self.workspace)
                    present[name] = (
                        'line 42' in prompt,                      # the code
                        'src/app.py' in prompt,                   # the location
                        'to address the review comment' in prompt,  # the guardrail
                        'revert this' in prompt,                  # the comment body
                    )
                self.assertEqual(
                    len(set(present.values())), 1,
                    f'transports disagree on review-comment context: {present}',
                )


if __name__ == '__main__':
    unittest.main()
