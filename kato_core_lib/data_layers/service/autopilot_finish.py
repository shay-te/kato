"""``kato:autopilot``: kato presses Done for the operator once the work is finished.

Done is the operator's own Done button — ``publish.finish_task_planning_session(
task, by_operator=True)``: push, pull request, the ticket's move, the Slack
post. Nothing here publishes in a way of its own.

The work is finished when:

* a chat (a ``kato:wait-planning`` ticket, a local task) — the agent printed
  ``<KATO_TASK_DONE>``, which a task on autopilot is told to do
  (``helpers/autopilot_guidance_utils.py``);
* a task kato ran by itself — it parked after its tests.

Then a ``kato:review-loop`` tag runs that loop first, and only a CLEAN loop
presses Done. A loop that ends any other way (stuck, out of rounds, stopped,
failed, nothing to review) leaves the task to the operator, as does a
``kato:wait-before-git-push`` tag: that tag says "ask me before pushing", and
it is the stricter statement. Whatever autopilot decides, it says so in the
task's status feed.
"""

from __future__ import annotations

import threading
from typing import Callable

from kato_core_lib.helpers.autopilot_store import is_autopilot
from kato_core_lib.helpers.late_binding import provider_for
from kato_core_lib.helpers.mission_logging_utils import log_mission_step

#: The review loop's verdict that lets autopilot press Done.
CLEAN_REVIEW = 'clean'


def _run_in_thread(work: Callable[[], object], name: str) -> None:
    threading.Thread(target=work, name=name, daemon=True).start()


class AutopilotFinisher(object):
    """Decides, at each "the work is finished" moment, whether to press Done."""

    def __init__(
        self,
        *,
        press_done: Callable[[str], dict],
        read_task: Callable[[str], object],
        review_rounds: Callable[[object], int],
        start_review_loop: Callable[[object], None],
        holds_push: Callable[[object], bool],
        runner_in_flight: Callable[[str], bool],
        logger,
        run_in_background: Callable[[Callable[[], object], str], None] = _run_in_thread,
    ) -> None:
        self._press_done = press_done
        self._read_task = read_task
        self._review_rounds = review_rounds
        self._start_review_loop = start_review_loop
        self._holds_push = holds_push
        self._runner_in_flight = runner_in_flight
        self._get_logger = provider_for(logger)
        self._background = run_in_background

    def agent_said_done(self, task_id: object) -> bool:
        """The agent printed the done token. True when autopilot took it over,
        and the caller must not publish by itself."""
        task_id = _norm(task_id)
        if not is_autopilot(task_id):
            return False
        if self._runner_in_flight(task_id):
            # A task kato runs by itself still has its tests to pass; it
            # parks after them, and that is where Done is pressed.
            self._say(task_id, 'autopilot: the agent is done; Done is pressed once its tests pass')
            return True
        try:
            task = self._read_task(task_id)
        except Exception:
            self._get_logger().exception('autopilot: could not read task %s', task_id)
            task = None
        if task is None:
            # Without the task its review-loop and push tags are unknown, and
            # guessing could push work the operator asked to see first.
            self._say(task_id, 'autopilot: could not read the task, so Done was not pressed — press it yourself')
            return True
        if self._leaves_push_to_operator(task_id, task):
            return True
        if self._review_rounds(task):
            self._say(task_id, 'autopilot: the agent is done; the review loop runs before Done')
            self._start_review_loop(task)
            return True
        self.press(task_id)
        return True

    def task_parked(self, task) -> dict | None:
        """A task kato ran by itself parked after its tests. Its review loop
        (if tagged) was started by the park. The Done result, or None."""
        task_id = _norm(getattr(task, 'id', ''))
        if not is_autopilot(task_id) or self._leaves_push_to_operator(task_id, task):
            return None
        if self._review_rounds(task):
            self._say(task_id, 'autopilot: Done is pressed once the review loop is clean')
            return None
        return self.press(task_id)

    def review_loop_finished(self, task_id: object, status: object) -> None:
        """A review loop ended. A clean one presses Done (in the background:
        this is called on the loop's own thread)."""
        task_id = _norm(task_id)
        if not is_autopilot(task_id):
            return
        verdict = str(getattr(status, 'value', status) or '')
        if verdict != CLEAN_REVIEW:
            self._say(
                task_id,
                f'autopilot: the review loop ended {verdict.replace("_", " ") or "without a verdict"}, '
                'so Done was not pressed — the task is yours',
            )
            return
        self._background(lambda: self.press(task_id), f'autopilot-done-{task_id}')

    def press(self, task_id: str) -> dict | None:
        """Press Done — the button's own call — and say how it went."""
        self._say(task_id, 'autopilot: pressing Done (push, pull request, ticket)')
        try:
            result = self._press_done(task_id) or {}
        except Exception:
            self._get_logger().exception('autopilot: Done failed for task %s', task_id)
            self._say(task_id, 'autopilot: Done failed — see the log; press it yourself')
            return None
        if result.get('finished'):
            where = 'Done' if result.get('moved_to_done') else 'In Review'
            self._say(task_id, f'autopilot: Done pressed — the task moved to {where}')
        else:
            why = str(result.get('move_error') or result.get('error') or 'nothing was published')
            self._say(task_id, f'autopilot: Done did not finish the task: {why}')
        return result

    def _leaves_push_to_operator(self, task_id: str, task) -> bool:
        if not self._holds_push(task):
            return False
        self._say(
            task_id,
            'autopilot: the task also asks to wait before pushing, so Done was not pressed — '
            'the task is yours',
        )
        return True

    def _say(self, task_id: str, message: str) -> None:
        log_mission_step(self._get_logger(), task_id, message)


def _norm(task_id: object) -> str:
    return str(task_id or '').strip()
