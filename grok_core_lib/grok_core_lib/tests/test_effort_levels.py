"""Grok's ``--reasoning-effort`` levels, read from ``grok --help``."""

from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from grok_core_lib.grok_core_lib.helpers.effort_levels import (
    FALLBACK_EFFORT_LEVELS,
    _parse_effort_levels_text,
    discover_effort_levels,
    reset_effort_levels_cache,
)

_RUN = 'grok_core_lib.grok_core_lib.helpers.effort_levels.subprocess.run'
_HELP = '''Options:
      --reasoning-effort <REASONING_EFFORT>
          Reasoning effort [possible values: low, medium, high]
      --sandbox <SANDBOX>
          Sandbox [possible values: off, workspace]
'''


class EffortLevelTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_effort_levels_cache()
        self.addCleanup(reset_effort_levels_cache)

    def test_levels_come_from_the_flag_s_own_help_block(self) -> None:
        with patch(_RUN, return_value=subprocess.CompletedProcess([], 0, _HELP, '')) as run:
            self.assertEqual(discover_effort_levels('grok'), ['low', 'medium', 'high'])
            self.assertEqual(discover_effort_levels('grok'), ['low', 'medium', 'high'])
        run.assert_called_once()

    def test_no_discovery_falls_back(self) -> None:
        with patch(_RUN, side_effect=OSError('missing')):
            self.assertEqual(discover_effort_levels('nope'), list(FALLBACK_EFFORT_LEVELS))

    def test_help_text_without_the_flag_or_values_parses_to_nothing(self) -> None:
        self.assertIsNone(_parse_effort_levels_text('no such flag'))
        self.assertIsNone(_parse_effort_levels_text('--reasoning-effort <X>\n      --other'))
        self.assertIsNone(_parse_effort_levels_text('--reasoning-effort [possible values: ???]'))


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
