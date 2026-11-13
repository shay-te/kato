"""Telling OpenCode V1 from V2, from ``opencode --version``."""

from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from opencode_core_lib.opencode_core_lib.helpers.cli_generation import (
    GENERATION_V1,
    GENERATION_V2,
    cli_generation,
    generation_flags,
    reset_cli_generation_cache,
)

_RUN = 'opencode_core_lib.opencode_core_lib.helpers.cli_generation.subprocess.run'


class CliGenerationTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_cli_generation_cache()
        self.addCleanup(reset_cli_generation_cache)

    def _detect(self, stdout: str) -> int:
        with patch(_RUN, return_value=subprocess.CompletedProcess([], 0, stdout, '')):
            return cli_generation(f'opencode-{stdout}')

    def test_the_major_version_decides(self) -> None:
        self.assertEqual(self._detect('1.19.5'), GENERATION_V1)
        self.assertEqual(self._detect('opencode 2.2.7'), GENERATION_V2)
        self.assertEqual(self._detect('3.0.0'), GENERATION_V2)

    def test_an_unreadable_version_is_taken_as_v2_so_v1_fails_loudly(self) -> None:
        self.assertEqual(self._detect('nightly'), GENERATION_V2)
        with patch(_RUN, side_effect=OSError('missing')):
            self.assertEqual(cli_generation('nowhere'), GENERATION_V2)

    def test_the_answer_is_cached_per_binary(self) -> None:
        with patch(_RUN, return_value=subprocess.CompletedProcess([], 0, '1.0.0', '')) as run:
            cli_generation('opencode')
            cli_generation('opencode')
        run.assert_called_once()
        self.assertIs(run.call_args.kwargs['stdin'], subprocess.DEVNULL)

    def test_flags_per_generation(self) -> None:
        self.assertEqual(generation_flags(GENERATION_V2), ['--standalone'])
        self.assertEqual(generation_flags(GENERATION_V1), [])


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
