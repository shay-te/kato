"""The lessons compaction one-shot must not run on the interactive budget.

The shared 120s default is sized for a one-shot a human is waiting on.
Compaction is neither interactive nor small: it merges the whole lessons file
with every pending per-task lesson, so its prompt grows with the operator's
history. Once it outgrew 120s it timed out on EVERY run:

    OneShotError: claude one-shot did not finish within 120s
    compact LLM call failed; leaving lesson files untouched

Which is the worst shape of failure — silent, in the background, and
self-worsening: the pending lessons never merge, so next time the prompt is
bigger and the timeout is even more certain. The agent quietly stops learning.
"""

from __future__ import annotations

import unittest

from agent_core_lib.agent_core_lib.helpers.one_shot import (
    DEFAULT_TIMEOUT_SECONDS,
)
from kato_core_lib.kato_core_lib import _LESSONS_ONE_SHOT_TIMEOUT_SECONDS


class LessonsOneShotTimeoutTests(unittest.TestCase):
    def test_the_budget_is_far_above_the_interactive_default(self) -> None:
        # Nothing waits on this call, so the only requirement is that it is
        # comfortably larger than a real merge takes.
        self.assertGreater(
            _LESSONS_ONE_SHOT_TIMEOUT_SECONDS, DEFAULT_TIMEOUT_SECONDS,
            'compaction must not inherit the interactive one-shot budget',
        )
        self.assertGreaterEqual(_LESSONS_ONE_SHOT_TIMEOUT_SECONDS, 600)

    def test_the_lessons_one_shot_is_built_with_it(self) -> None:
        """The constant only matters if the factory actually receives it."""
        import inspect
        from kato_core_lib import kato_core_lib as module

        source = inspect.getsource(module)
        start = source.index('llm_one_shot = make_one_shot(')
        call = source[start:source.index(')', start)]
        self.assertIn('timeout_seconds=_LESSONS_ONE_SHOT_TIMEOUT_SECONDS', call)


if __name__ == '__main__':
    unittest.main()
