"""Test failures the task did not cause.

A failing test is sent back to the chat to be fixed — unless it already failed
the same way before the task, on the branch the task starts from. Those used to
be sent anyway: a loop spent all three fix turns on four tests the chat reported
"same on master" each time (the operator had already filed them as follow-up
tasks) and ended STUCK on code the task never touched. The chat now reports them
apart, in ``already_failing``: shown, never sent to be fixed, never a failure —
the same treatment as a test that cannot even run here (``cannot_run``).

That list is the chat's word. What can be checked here, is: a reported test that
names a file this task changed is not an older failure, whatever the chat says.
It goes back to the failures, with a note saying why.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import replace

from review_loop_core_lib.review_loop_core_lib.data.state import TestReport
from review_loop_core_lib.review_loop_core_lib.diff import split_file_diffs
from review_loop_core_lib.review_loop_core_lib.ports import RepoDiff

CHANGED_BY_TASK_NOTE = ' (this task changed a file it names, so it is not an older failure)'

# A path counts only as a whole path: ``index.js`` changed at a repository's
# root says nothing about a failure in ``src/app/index.js``.
_PATH_CHAR = r'[\w./\\-]'


def changed_paths(diffs: Iterable[RepoDiff]) -> set[str]:
    """Every file path the task's change touches, in every repository."""
    return {path for repo in diffs for path, _ in split_file_diffs(repo.diff)}


def hold_to_the_task(report: TestReport, changed: Iterable[str]) -> TestReport:
    """``report``, with each "already failing" test that names a changed file
    moved back to its failures (and the run then failed)."""
    patterns = [
        re.compile(rf'(?<!{_PATH_CHAR}){re.escape(path)}(?!{_PATH_CHAR})')
        for path in sorted({str(path) for path in changed if path})
    ]
    kept: list[str] = []
    moved: list[str] = []
    for item in report.already_failing:
        (moved if any(pattern.search(item) for pattern in patterns) else kept).append(item)
    if not moved:
        return report
    return replace(
        report,
        passed=False,
        failures=[*report.failures, *(item + CHANGED_BY_TASK_NOTE for item in moved)],
        already_failing=kept,
    )
