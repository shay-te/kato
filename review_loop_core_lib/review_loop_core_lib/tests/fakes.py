"""Stand-ins for what a host provides: its chat, its reviewer, its diffs.

They behave like the real thing at the boundary the loop sees — a chat that
is busy or free, that turns a delivered prompt into a finished turn; a
reviewer that answers or can be stopped mid-review — so the loop's own logic
runs for real.
"""

from __future__ import annotations

import json
import threading
import time
from contextlib import AbstractContextManager

from review_loop_core_lib.review_loop_core_lib.ports import (
    ChatReadiness,
    ChatTurnEnd,
    LoopWording,
    RepoDiff,
)
from review_loop_core_lib.review_loop_core_lib.reviewer_prompt import (
    VERDICT_CLOSE,
    VERDICT_OPEN,
)
from review_loop_core_lib.review_loop_core_lib.runner import RunnerOptions

FAST = RunnerOptions(
    poll_seconds=0.005,
    settle_seconds=0.0,
    chat_wait_timeout_seconds=5.0,
    fix_turn_timeout_seconds=5.0,
    session_gone_grace_seconds=0.05,
)


def wrap(text: str, source: str) -> str:
    return f'<untrusted source="{source}">\n{text}\n</untrusted>'


WORDING = LoopWording(
    wrap_untrusted=wrap,
    findings_header='Host review loop — round {round} of {max_rounds}',
    stage_header='Host review loop — {stage}',
    reviewer_guidance='Read the rules file first.',
    findings_guidance='Never print the done marker.',
)


def finding(severity: str, *, file: str = 'app.py', symbol: str = 'run',
            category: str = 'correctness', title: str = 'a bug', repo: str = 'api') -> dict:
    return {
        'severity': severity, 'repository': repo, 'file': file, 'line': 3,
        'symbol': symbol, 'category': category, 'title': title, 'detail': 'fix it',
    }


def reply(*findings: dict) -> str:
    """A reviewer reply: some prose, then the verdict block."""
    body = json.dumps({'findings': list(findings)})
    return f'Report:\n- see below\n\n{VERDICT_OPEN}\n{body}\n{VERDICT_CLOSE}\n'


class FakeTree(object):
    """The task's working tree, as the diff source reports it."""

    def __init__(self, files: dict[str, str] | None = None) -> None:
        self.files = dict(files or {'app.py': 'print(1)\n'})
        self.calls = 0

    def diff_source(self, task_id: str) -> list[RepoDiff]:
        self.calls += 1
        text = ''.join(
            f'diff --git a/{path} b/{path}\n+{content}' for path, content in self.files.items()
        )
        return [RepoDiff(repo_id='api', diff=text, base='main', head=task_id, cwd='/w/api')]


class FakeChat(object):
    """A task chat: readiness is scripted; a delivered prompt becomes a turn.

    ``reply`` is the fix turn's final text — a string, or ``prompt -> str``
    (to answer with a response block naming the ids it was sent).
    """

    def __init__(self, *, fixer=None, turn_error: bool = False, reply='Fixed them.') -> None:
        self.readiness_script: list[ChatReadiness] = []
        self.default_readiness = ChatReadiness.ready()
        self.delivered: list[tuple[str, bool]] = []
        self.turns: list[ChatTurnEnd] = []
        self.fixer = fixer
        self.turn_error = turn_error
        self.alive = True
        self.stalled = False
        self.awaiting_approval = ''
        self.answer_turns = True
        self.reply = reply
        self.lock = threading.Lock()
        self.lock_holders: list[str] = []

    def readiness(self, task_id: str) -> ChatReadiness:
        if self.readiness_script:
            return self.readiness_script.pop(0)
        return self.default_readiness

    def dispatch_lock(self, task_id: str) -> AbstractContextManager:
        self.lock_holders.append(threading.current_thread().name)
        return self.lock

    def is_stalled(self, task_id: str) -> bool:
        return self.stalled

    def session_alive(self, task_id: str) -> bool:
        return self.alive

    def pending_approval(self, task_id: str) -> str:
        return self.awaiting_approval

    def deliver(self, task_id: str, prompt: str, *, force_respawn: bool) -> bool:
        self.delivered.append((prompt, force_respawn))
        if self.answer_turns:
            if self.fixer is not None:
                self.fixer(prompt)
            text = self.reply(prompt) if callable(self.reply) else self.reply
            self.turns.append(ChatTurnEnd(
                received_at=time.time() + 0.001, is_error=self.turn_error, text=text,
            ))
        return True

    def turn_end_since(self, task_id: str, since_epoch: float) -> ChatTurnEnd | None:
        for turn in reversed(self.turns):
            if turn.received_at > since_epoch:
                return turn
        return None


class FakeReviewer(object):
    """Answers each review with the next scripted reply (the last one repeats)."""

    def __init__(self, *replies: str, block_until_cancelled: bool = False) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []
        self.block_until_cancelled = block_until_cancelled
        self.started = threading.Event()

    def review(self, prompt: str, *, task_id: str, cancel_event: threading.Event) -> str:
        self.prompts.append(prompt)
        self.started.set()
        if self.block_until_cancelled:
            cancel_event.wait(5)
            raise RuntimeError('the review process was killed')
        if not self.replies:
            raise RuntimeError('no scripted reply left')
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


def wait_until(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()
