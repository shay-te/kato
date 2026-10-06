"""The UI recognises the review loop's chat message by its header — keep them in step.

``REVIEW_LOOP_FINDINGS_HEADER`` (Python) builds the first line of every message
the loop posts; ``reviewLoopPrompt.js`` (UI) finds that line to show the
message as kato's instead of "You asked". If either side's wording drifts, the
UI silently goes back to putting kato's words in the operator's mouth — so this
reads the JS pattern straight out of the source and runs it against what the
Python side really produces.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from review_loop_core_lib.review_loop_core_lib.findings_prompt import (
    build_findings_prompt,
)
from review_loop_core_lib.review_loop_core_lib.ports import LoopWording
from review_loop_core_lib.review_loop_core_lib.verdict import parse_review_verdict

from kato_core_lib.helpers.review_loop_guidance import REVIEW_LOOP_FINDINGS_HEADER

_JS = (
    Path(__file__).resolve().parents[1]
    / 'webserver' / 'ui' / 'src' / 'components' / 'reviewLoop' / 'reviewLoopPrompt.js'
)


def _ui_pattern() -> re.Pattern:
    source = _JS.read_text(encoding='utf-8')
    match = re.search(r'REVIEW_LOOP_HEADER_PATTERN = /(.+)/m;', source)
    assert match, 'REVIEW_LOOP_HEADER_PATTERN not found in reviewLoopPrompt.js'
    return re.compile(match.group(1), re.MULTILINE)


class HeaderPinTests(unittest.TestCase):

    def test_the_ui_pattern_matches_the_header_python_writes(self) -> None:
        header = REVIEW_LOOP_FINDINGS_HEADER.format(round=3, max_rounds=5)
        match = _ui_pattern().search(header)
        self.assertIsNotNone(match, header)
        self.assertEqual(match.groups(), ('3', '5'))

    def test_it_matches_a_real_findings_message_behind_a_preamble(self) -> None:
        verdict = parse_review_verdict(
            '<review-verdict>{"findings": [{"severity": "MAJOR", "title": "x"}]}</review-verdict>',
        )
        message = build_findings_prompt(
            task_id='T-1', verdict=verdict, round_number=2, max_rounds=5,
            wording=LoopWording(
                wrap_untrusted=lambda text, source: text,
                findings_header=REVIEW_LOOP_FINDINGS_HEADER,
            ),
        )
        respawned = 'WORKSPACE SCOPE — STRICT BOUNDARY\n...\n\n' + message
        self.assertEqual(_ui_pattern().search(respawned).groups(), ('2', '5'))


if __name__ == '__main__':
    unittest.main()
