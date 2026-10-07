"""The safeguards-refusal detector: it fires on the real message, pulls out
the request id / details / model, and does not fire on ordinary errors."""
from __future__ import annotations

import unittest

from claude_core_lib.claude_core_lib.helpers.safeguard_error import (
    SafeguardFlag,
    classify_safeguard_error,
    is_safeguard_flag,
)

# The message as the CLI surfaces it (from a real occurrence).
REAL = (
    "API Error: Opus 5.5's safeguards flagged this session "
    "(https://www.anthropic.com/legal/aup). You may be seeing this for the "
    "first time: Opus 5.5 is more capable and has stronger safeguards as a "
    "result, which can sometimes flag non-cybersecurity work.\n\n"
    "Try rephrasing the request in a new session or change your model.\n\n"
    "Learn more: https://support.claude.com/en/articles/8106465\n\n"
    "Details: [cyber]\n\nRequest ID: req_011CfkrVkTS5AF84rGjSAjWr\n"
    "Message ID: msg_011CfkrVkviPcGEVGzwHjq2A"
)


class ClassifyTests(unittest.TestCase):

    def test_the_real_message_is_recognised_and_parsed(self) -> None:
        flag = classify_safeguard_error(REAL)
        self.assertEqual(flag, SafeguardFlag(
            details='cyber',
            request_id='req_011CfkrVkTS5AF84rGjSAjWr',
            model='Opus 5.5',
        ))
        self.assertTrue(is_safeguard_flag(REAL))

    def test_a_reworded_message_with_a_different_model_still_matches(self) -> None:
        # The model name drifts and the prose changes; the AUP link + "flagged
        # this session" are the stable anchors.
        text = ("Error: Fable 6's safeguards flagged the session — see "
                "https://www.anthropic.com/legal/aup for details.")
        flag = classify_safeguard_error(text)
        self.assertIsNotNone(flag)
        self.assertEqual(flag.model, 'Fable 6')
        self.assertEqual(flag.details, '')
        self.assertEqual(flag.request_id, '')

    def test_the_anchors_can_appear_in_joined_result_plus_stderr(self) -> None:
        joined = 'turn failed\n---stderr---\n' + REAL
        self.assertTrue(is_safeguard_flag(joined))

    def test_ordinary_errors_are_not_mistaken_for_a_flag(self) -> None:
        for text in (
            '',
            None,
            'API Error: 529 overloaded_error',
            'rate limit exceeded; retry after 30s',
            'No conversation found with session ID: abc',
            # The AUP link alone, without the "flagged" anchor, is not enough.
            'see our policy at https://www.anthropic.com/legal/aup',
            # "flagged" alone, without the AUP link, is not enough either.
            'your account was flagged this session for billing',
        ):
            with self.subTest(text=text):
                self.assertFalse(is_safeguard_flag(text))
                self.assertIsNone(classify_safeguard_error(text))


if __name__ == '__main__':
    unittest.main()
