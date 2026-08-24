"""The effort levels this transport reports, and why it reports them statically.

Mirrors ``claude_core_lib.tests.test_effort_levels`` in shape, because the
module mirrors its API. What it must NOT mirror is the discovery behaviour:
Codex has no ``--effort`` flag, so there is nothing to parse out of the
binary's help. These tests pin that difference deliberately — the answer is
static, it never shells out, and it is never empty.
"""

from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from codex_core_lib.codex_core_lib.cli_client import CodexCliClient
from codex_core_lib.codex_core_lib.helpers.effort_levels import (
    FALLBACK_EFFORT_LEVELS,
    discover_effort_levels,
    reset_effort_levels_cache,
)


class DiscoverEffortLevelsTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_effort_levels_cache()
        self.addCleanup(reset_effort_levels_cache)

    def test_returns_the_supported_levels(self) -> None:
        self.assertEqual(discover_effort_levels(), list(FALLBACK_EFFORT_LEVELS))

    def test_never_shells_out_to_the_binary(self) -> None:
        # The whole reason this module is static: probing ``codex --help`` for
        # an ``--effort`` flag would always come back empty, and a failed
        # probe on every picker render is pure latency.
        with patch.object(subprocess, 'run') as run:
            discover_effort_levels('codex')

        run.assert_not_called()

    def test_the_binary_and_timeout_arguments_are_accepted_and_ignored(self) -> None:
        # Present for signature parity with the other transports; a caller
        # holding a backend must be able to call them all the same way.
        self.assertEqual(
            discover_effort_levels('/opt/homebrew/bin/codex', timeout=0.001),
            list(FALLBACK_EFFORT_LEVELS),
        )

    def test_the_result_is_never_empty(self) -> None:
        # An empty picker reads to the operator as "this backend has no effort
        # control", which is not what "no discovery source" means.
        self.assertTrue(discover_effort_levels())

    def test_the_caller_cannot_mutate_the_module_state(self) -> None:
        levels = discover_effort_levels()
        levels.append('nonsense')

        self.assertNotIn('nonsense', discover_effort_levels())

    def test_reset_is_a_no_op_that_callers_can_still_call(self) -> None:
        # Nothing is cached, but a CLI-upgrade path resets every backend
        # uniformly rather than asking which ones cache.
        self.assertIsNone(reset_effort_levels_cache())
        self.assertTrue(discover_effort_levels())


class ClientAgreementTests(unittest.TestCase):
    """The reported levels must be the ones the client actually accepts."""

    def test_every_reported_level_is_accepted_by_the_client(self) -> None:
        for level in discover_effort_levels():
            with self.subTest(level=level):
                self.assertIn(level, CodexCliClient.SUPPORTED_EFFORT_LEVELS)

    def test_the_client_accepts_nothing_the_picker_does_not_offer(self) -> None:
        # Otherwise an operator could configure an effort the picker never
        # shows, or the picker offers one the client rejects at spawn.
        self.assertEqual(
            set(discover_effort_levels()),
            set(CodexCliClient.SUPPORTED_EFFORT_LEVELS),
        )


if __name__ == '__main__':
    unittest.main()
