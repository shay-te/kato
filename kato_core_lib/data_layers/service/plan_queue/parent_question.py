"""A plan sub-task's question, put to the chat its plan was split in.

The sub-task's agent asks its operator something (AskUserQuestion) that the
plan's own conversation may already have settled — "which way did we decide?".
That chat proposed the split and holds the reasons, so the operator can ask it
and read its answer beside the question. The operator still answers the
sub-task themselves: kato never answers an agent's question.

One question per sub-task at a time, kept in memory (a restart forgets it —
the answer is in the parent's chat anyway).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from kato_core_lib.data_layers.service.chat_question import ChatQuestionCancelled
from kato_core_lib.helpers.late_binding import provider_for
from kato_core_lib.helpers.plan_queue_guidance import plan_question_message

MAX_QUESTIONS = 8
MAX_FIELD_CHARS = 4000

ASKING = 'asking'
ANSWERED = 'answered'
FAILED = 'failed'
CANCELLED = 'cancelled'


class ParentQuestionError(ValueError):
    """Refused: ``kind`` is 'not_found' (no plan knows the task), 'conflict'
    (a question is already out) or 'invalid' (the questions can't be read)."""

    def __init__(self, message: str, kind: str) -> None:
        super().__init__(message)
        self.kind = kind


def questions_text(questions) -> str:
    """The agent's AskUserQuestion questions as plain text for the chat."""
    if not isinstance(questions, list) or not questions or len(questions) > MAX_QUESTIONS:
        raise ParentQuestionError(f'send 1 to {MAX_QUESTIONS} questions', 'invalid')
    return '\n\n'.join(_question_block(number, question) for number, question in enumerate(questions, start=1))


def _question_block(number: int, question) -> str:
    if not isinstance(question, dict) or not _text(question.get('question')):
        raise ParentQuestionError(f'question {number} has no text', 'invalid')
    header = _text(question.get('header'))
    lines = [f'{number}. {f"[{header}] " if header else ""}{_text(question.get("question"))}']
    options = question.get('options') if isinstance(question.get('options'), list) else []
    lines.extend(line for line in map(_option_line, options) if line)
    if question.get('multiSelect'):
        lines.append('   (more than one may be chosen)')
    return '\n'.join(lines)


def _option_line(option) -> str:
    label = _text(option.get('label')) if isinstance(option, dict) else ''
    if not label:
        return ''
    description = _text(option.get('description'))
    return f'   - {label}{f" — {description}" if description else ""}'


def _text(value) -> str:
    return str(value or '').strip()[:MAX_FIELD_CHARS] if isinstance(value, str) else ''


def _start_daemon(target: Callable[[], None]) -> None:
    threading.Thread(target=target, name='plan-question', daemon=True).start()


class ParentQuestions(object):
    def __init__(
        self, *, queue, question, clock: Callable[[], float] = time.time,
        start: Callable[[Callable[[], None]], None] = _start_daemon,
    ) -> None:
        self._get_queue = provider_for(queue)
        self._question = question
        self._clock = clock
        self._start = start
        self._lock = threading.Lock()
        self._asked: dict[str, dict] = {}
        self._cancels: dict[str, threading.Event] = {}

    def parent_of(self, task_id: str) -> str:
        """The task ``task_id``'s plan was split from ('' = no plan knows it)."""
        queue = self._get_queue()
        plan = queue.plan_for_task(task_id) if queue is not None else None
        return plan.source_task_id if plan is not None else ''

    def state(self, task_id: str) -> dict | None:
        with self._lock:
            asked = self._asked.get(task_id)
            return dict(asked) if asked is not None else None

    def ask(self, task_id: str, questions) -> dict:
        parent = self.parent_of(task_id)
        if not parent:
            raise ParentQuestionError(f'{task_id} is not one of a plan\'s tasks', 'not_found')
        message = plan_question_message(task_id, questions_text(questions))
        cancel = threading.Event()
        with self._lock:
            if (self._asked.get(task_id) or {}).get('status') == ASKING:
                raise ParentQuestionError(f'{parent}\'s chat is already being asked', 'conflict')
            self._asked[task_id] = {
                'parent': parent, 'status': ASKING, 'answer': '', 'error': '', 'asked_at': self._clock(),
            }
            self._cancels[task_id] = cancel
        self._start(lambda: self._run(task_id, parent, message, cancel))
        return self.state(task_id)

    def cancel(self, task_id: str) -> dict | None:
        """Stop waiting for the answer — at once, so it can be asked again."""
        with self._lock:
            cancel = self._cancels.pop(task_id, None)
            if cancel is not None:
                cancel.set()
                self._asked[task_id].update(status=CANCELLED)
        return self.state(task_id)

    def shutdown(self) -> None:
        with self._lock:
            cancels = list(self._cancels.values())
        for cancel in cancels:
            cancel.set()

    def _run(self, task_id: str, parent: str, message: str, cancel: threading.Event) -> None:
        try:
            answer = self._question.ask(parent, message, cancel_event=cancel)
        except ChatQuestionCancelled:
            self._finish(task_id, cancel, status=CANCELLED)
        except Exception as exc:  # shown beside the question: why it has no answer
            self._finish(task_id, cancel, status=FAILED, error=str(exc) or exc.__class__.__name__)
        else:
            self._finish(task_id, cancel, status=ANSWERED, answer=answer)

    def _finish(self, task_id: str, cancel: threading.Event, *, status: str, answer: str = '',
                error: str = '') -> None:
        with self._lock:
            if self._cancels.get(task_id) is not cancel:
                return  # a newer question replaced this one
            self._cancels.pop(task_id, None)
            self._asked[task_id].update(status=status, answer=answer, error=error)
