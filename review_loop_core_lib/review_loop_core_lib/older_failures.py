"""Test failures the task did not cause.

A failing test is sent back to the chat to be fixed — unless it already failed
the same way before the task, on the branch the task starts from. Those used to
be sent anyway: a loop spent all three fix turns on four tests the chat reported
"same on master" each time (the operator had already filed them as follow-up
tasks) and ended STUCK on code the task never touched. The chat now reports them
apart, in ``already_failing``: shown, never sent to be fixed, never a failure —
the same treatment as a test that cannot even run here (``cannot_run``).

That list is the chat's word. What can be checked here, is: a reported test
whose own TEST FILE this task added or changed is this task's test, whatever the
chat says. It goes back to the failures, with a note saying why.

Only test files count. A test that merely imports code the task changed can
still be an older failure — the same failure with the task's changes stashed is
what shows it, and the chat checks that. The first version moved back any test
that named any changed file, and told the chat so: it kept four tests it had
proven older ("same failure with this task's changes stashed") in its failures,
because each imported a changed file, and the loop ended STUCK again.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import replace

from review_loop_core_lib.review_loop_core_lib.data.state import TestReport
from review_loop_core_lib.review_loop_core_lib.diff import split_file_diffs
from review_loop_core_lib.review_loop_core_lib.ports import RepoDiff

CHANGED_BY_TASK_NOTE = ' (this task added or changed its test file, so it is not an older failure)'

_TEST_FOLDERS = frozenset({'test', 'tests', '__tests__', 'spec', 'specs', '__mocks__', '__snapshots__'})
_TEST_NAMES = (
    re.compile(r'^(?:tests?|conftest)(?:[_.-]|$)', re.IGNORECASE),  # test_a.py, tests.py, conftest.py
    re.compile(r'[_.-](?:tests?|specs?)(?:[_.-]|$)', re.IGNORECASE),  # a_test.go, a.test.js, a.spec.ts
    re.compile(r'[a-z0-9](?:Tests?|Spec)[_.-]'),  # ATest.java, ATests.cs, setupTests.js
)

# A path counts only as a whole path: ``test_a.py`` changed at a repository's
# root says nothing about a failure in ``tests/test_a.py``. A path with a folder
# in it may follow a repository's name (``client/src/__tests__/a.test.js``).
_PATH_CHAR = r'[\w./\\-]'
_NOT_AFTER_A_PATH = rf'(?<!{_PATH_CHAR})'
_NOT_AFTER_A_NAME = r'(?<![\w.\\-])'


def changed_paths(diffs: Iterable[RepoDiff]) -> set[str]:
    """Every file path the task's change touches, in every repository."""
    return {path for repo in diffs for path, _ in split_file_diffs(repo.diff)}


def is_test_file(path: str) -> bool:
    """Is ``path`` a test, or test code (a file in a tests folder)?"""
    *folders, name = path.replace('\\', '/').split('/')
    return (any(folder.lower() in _TEST_FOLDERS for folder in folders)
            or any(pattern.search(name) for pattern in _TEST_NAMES))


def hold_to_the_task(report: TestReport, changed: Iterable[str]) -> TestReport:
    """``report``, with each "already failing" test that names a test file this
    task changed moved back to its failures (and the run then failed)."""
    patterns = [
        _whole_path(path)
        for path in sorted({str(path) for path in changed if path and is_test_file(str(path))})
    ]
    kept: list[str] = []
    moved: list[str] = []
    for item in report.already_failing:
        named = item.replace('\\', '/')
        (moved if any(pattern.search(named) for pattern in patterns) else kept).append(item)
    if not moved:
        return report
    return replace(
        report,
        passed=False,
        failures=[*report.failures, *(item + CHANGED_BY_TASK_NOTE for item in moved)],
        already_failing=kept,
    )


def _whole_path(path: str) -> re.Pattern[str]:
    before = _NOT_AFTER_A_NAME if '/' in path else _NOT_AFTER_A_PATH
    return re.compile(rf'{before}{re.escape(path)}(?!{_PATH_CHAR})')
