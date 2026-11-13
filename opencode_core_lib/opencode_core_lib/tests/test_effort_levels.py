"""OpenCode's effort levels: model variants, a static set."""

from __future__ import annotations

import unittest

from opencode_core_lib.opencode_core_lib.helpers.effort_levels import (
    FALLBACK_EFFORT_LEVELS,
    discover_effort_levels,
    reset_effort_levels_cache,
)


class EffortLevelTests(unittest.TestCase):
    def test_the_static_set_is_offered_and_never_empty(self) -> None:
        self.assertEqual(discover_effort_levels('anything', timeout=0.1), list(FALLBACK_EFFORT_LEVELS))
        self.assertIn('high', FALLBACK_EFFORT_LEVELS)
        self.assertIsNone(reset_effort_levels_cache())


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
