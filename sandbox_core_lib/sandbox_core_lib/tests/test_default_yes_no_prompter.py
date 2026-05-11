"""Tests for the inline _default_yes_no_prompter in bypass_permissions_validator.

The prompter is a security gate: it must loop on invalid input and
never treat a bare Enter as yes (the default is always False when
EOFError / KeyboardInterrupt is raised).
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

from sandbox_core_lib.sandbox_core_lib.bypass_permissions_validator import (
    _default_yes_no_prompter,
)


class DefaultYesNoPrompterTests(unittest.TestCase):
    def _prompt(self, responses: list[str]) -> bool:
        """Call the prompter with a sequence of mocked input() responses."""
        with patch('builtins.input', side_effect=responses):
            return _default_yes_no_prompter('Are you sure?')

    def test_yes_returns_true(self):
        self.assertTrue(self._prompt(['yes']))

    def test_y_returns_true(self):
        self.assertTrue(self._prompt(['y']))

    def test_no_returns_false(self):
        self.assertFalse(self._prompt(['no']))

    def test_n_returns_false(self):
        self.assertFalse(self._prompt(['n']))

    def test_uppercase_yes_returns_true(self):
        self.assertTrue(self._prompt(['YES']))

    def test_uppercase_no_returns_false(self):
        self.assertFalse(self._prompt(['NO']))

    def test_mixed_case_yes_returns_true(self):
        self.assertTrue(self._prompt(['Yes']))

    def test_stray_enter_loops_then_accepts_yes(self):
        # A bare Enter ('') must NOT default to yes; the prompter loops.
        self.assertTrue(self._prompt(['', '', 'y']))

    def test_invalid_answer_loops_until_valid(self):
        self.assertFalse(self._prompt(['maybe', 'later', 'n']))

    def test_eof_error_returns_false(self):
        with patch('builtins.input', side_effect=EOFError):
            self.assertFalse(_default_yes_no_prompter('Continue?'))

    def test_keyboard_interrupt_returns_false(self):
        with patch('builtins.input', side_effect=KeyboardInterrupt):
            self.assertFalse(_default_yes_no_prompter('Continue?'))

    def test_whitespace_around_answer_is_stripped(self):
        self.assertTrue(self._prompt(['  yes  ']))

    def test_default_is_not_accepted_on_empty_input(self):
        # Security property: empty input must never accept the action.
        # The prompter loops, so we follow with 'n' to end the test.
        self.assertFalse(self._prompt(['', 'n']))
