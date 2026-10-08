"""The loop's other messages into the main chat: the self-check and the tests.

Besides the findings (``findings_prompt.py``) the loop asks the main chat for
two more things, each ending in a machine-readable block it parses
(``response.py``):

* a **self-check** before any independent review — the chat already holds the
  whole context, so having it review and fix its own change first is cheap
  and catches the easy bugs before a reviewer is paid to;
* a **test run** once a review comes back clean — "clean" means no blocking
  finding AND the tests pass. Failing tests go back to the chat to fix
  (``build_tests_fix_prompt``) and the loop goes on.

A loop RESUMED while the chat was mid-fix sends a short **continue** nudge
instead of the round's message again (``build_continue_prompt``).

Each message's FIRST line is the host's ``stage_header`` so a UI can show it
as the loop's, not the operator's. Text the chat did not get from the
operator (test failures) is framed as untrusted.
"""

from __future__ import annotations

from review_loop_core_lib.review_loop_core_lib.findings_prompt import RESPONSE_OPEN
from review_loop_core_lib.review_loop_core_lib.ports import LoopWording
from review_loop_core_lib.review_loop_core_lib.reviewer_prompt import CHECKLIST

SELF_CHECK_OPEN = '<self-check>'
SELF_CHECK_CLOSE = '</self-check>'
TEST_REPORT_OPEN = '<test-report>'
TEST_REPORT_CLOSE = '</test-report>'


def stage_header(wording: LoopWording, stage: str) -> str:
    return wording.stage_header.format(stage=stage)


def build_self_check_prompt(*, turn: int, max_turns: int, wording: LoopWording) -> str:
    """Ask the chat to review its own whole change and fix what it finds."""
    return _with_guidance(f'''{stage_header(wording, f"self-check {turn} of {max_turns}")}

Before an independent reviewer looks at this change, review it yourself — you
have the full context. Go through the WHOLE change (every repository, against
its base branch, including new and uncommitted files) with this checklist:

{CHECKLIST}

- Fix every real BLOCKER / MAJOR problem you find, at its root cause, with a
  regression test for each fix, and run the tests that cover it.
- Do not commit, push or run git — publishing is the operator's call.
- Do not chase style nits.

End your reply with exactly one self-check block and nothing after it:

{SELF_CHECK_OPEN}
{{"clean": false, "fixed": 2, "summary": "what you found and fixed"}}
{SELF_CHECK_CLOSE}

`clean` is true only when this pass found nothing more to fix.''', wording)


def build_tests_prompt(*, wording: LoopWording) -> str:
    """Ask the chat to run the task's tests on the current tree and report."""
    return _with_guidance(f'''{stage_header(wording, "run the tests")}

The independent review found no blocking issues. Before the loop can finish,
run this task's tests — and any linters or type checks the project uses — on
the current working tree, and report the result. Do NOT change code in this
turn: only run and report.

End your reply with exactly one test report block and nothing after it:

{TEST_REPORT_OPEN}
{{"passed": true, "command": "the command(s) you ran", "summary": "41 passed, 0 failed", "failures": []}}
{TEST_REPORT_CLOSE}

`passed` is false if anything failed; list each failure (test name and a
one-line reason) in `failures`. If the project has no tests, say so in
`summary` and set `passed` to true.''', wording)


def build_tests_fix_prompt(*, task_id: str, failures: list[str], wording: LoopWording) -> str:
    """Send failing tests back to the chat to fix; the next review re-checks."""
    listed = '\n'.join(f'- {failure}' for failure in failures) or '- (no details reported)'
    framed = wording.wrap_untrusted(listed, f'task {task_id} failing tests')
    return _with_guidance(f'''{stage_header(wording, "fix the failing tests")}

The task's tests are failing:

{framed}

Fix the code so they pass — at the root cause. If a test itself is wrong, fix
the test and say why. Run the tests again before you finish. Do not commit,
push or run git.''', wording)


def build_continue_prompt(
    *, round_number: int, failing_tests: bool, wording: LoopWording,
) -> str:
    """Nudge the chat to finish a fix the loop was cut off waiting for.

    The round's message is already in the chat, so it is not sent again: a
    resumed loop asks the chat to carry on — the review loop's own "continue"
    — and still needs the answer block the round asked for, because that
    block is the decision ledger the next review is told.
    """
    if failing_tests:
        work = f"fixing the failing tests from round {round_number}"
        ending = 'Run the tests again before you finish.'
    else:
        work = f"round {round_number}'s findings"
        ending = (
            f'End your reply with the {RESPONSE_OPEN} block that message asked for '
            '— one entry per finding id — and nothing after it.'
        )
    return _with_guidance(f'''{stage_header(wording, f"continue round {round_number}")}

The review loop stopped while you were working on {work} (that message is
earlier in this chat). Continue from where you stopped and finish it. Do not
commit, push or run git. {ending}''', wording)


def _with_guidance(prompt: str, wording: LoopWording) -> str:
    guidance = wording.findings_guidance.strip()
    return f'{prompt}\n{guidance}\n' if guidance else f'{prompt}\n'
