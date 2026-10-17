"""The UI reads a plan sub-task's parent off its ticket summary — keep them in step.

``subtask_summary`` (Python) titles every sub-task the plan queue creates
"Subtask of UNA-1: <title>"; ``subtaskLabel.js`` (UI) splits that title so the
tab shows the parent as a fixed mark and only the name as the label. If either
side's wording drifts, sub-task tabs silently lose the mark and show the long
prefix as their name again — so this reads the JS pattern straight out of the
source and runs it against what the Python side really writes.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from plan_queue_core_lib.plan_queue_core_lib.ports import LaunchRequest

from kato_core_lib.data_layers.service.plan_queue.step_footer import subtask_summary

_JS = (
    Path(__file__).resolve().parents[1]
    / 'webserver' / 'ui' / 'src' / 'components' / 'planQueue' / 'subtaskLabel.js'
)


def _ui_pattern() -> re.Pattern:
    match = re.search(r'SUBTASK_SUMMARY_PATTERN = /(.+)/;', _JS.read_text(encoding='utf-8'))
    assert match, 'SUBTASK_SUMMARY_PATTERN not found in subtaskLabel.js'
    return re.compile(match.group(1))


class SubtaskSummaryPinTests(unittest.TestCase):
    def test_the_ui_pattern_splits_the_title_python_writes(self) -> None:
        request = LaunchRequest(
            plan_id='p' * 32, step_id='events', index=15, count=17, title='Event registration',
            description='Do it.', repositories=(), source_task_id='UNA-3267', plan_title='Plan',
        )
        match = _ui_pattern().match(subtask_summary(request))
        self.assertIsNotNone(match)
        self.assertEqual(match.groups(), ('UNA-3267', 'Event registration'))

    def test_an_ordinary_summary_is_left_alone(self) -> None:
        self.assertIsNone(_ui_pattern().match('Fix the login page'))


if __name__ == '__main__':
    unittest.main()
