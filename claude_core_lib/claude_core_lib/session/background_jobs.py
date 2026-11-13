"""Which background jobs a Claude session is still waiting on — read from its own events.

A turn can start work that outlives it: a Bash/agent call with
``run_in_background``, a ``Monitor``, a ``Workflow``. While one of those runs,
the agent is still working even though its turn has ended. The CLI says when
each one starts and ends::

    {"type": "system", "subtype": "task_started", "task_id": "b1f…",
     "tool_use_id": "toolu_…", "is_backgrounded": true}
    {"type": "system", "subtype": "task_updated", "task_id": "b1f…",
     "patch": {"status": "completed"}}
    {"type": "system", "subtype": "task_notification", "task_id": "b1f…",
     "tool_use_id": "toolu_…", "status": "completed"}
    {"type": "system", "subtype": "background_tasks_changed", "tasks": [...]}

``background_tasks_changed`` is the CLI's own list of what still runs; the
others name one job. An older CLI said only ``<task-notification>`` in a user
message (naming ``<tool-use-id>``), and the job's start is known from the tool
call itself — both are still read.

A job is outstanding from its start until it is REPORTED finished — in the
same turn or a later one. It used to be "the last turn used a background tool",
which never ended for a job that finished inside its own turn: a session that
started an ``npm install`` in the background, read its log and finished, read
"working" for good, and everything waiting for it to be free (the host's run,
its review) waited until it timed out.

``webserver/ui/src/utils/backgroundJobs.js`` applies the same rule in the
browser; ``tests/test_background_jobs_parity.py`` runs both on the same cases.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

#: Tools whose call starts work that outlives the turn.
BACKGROUND_TOOLS = frozenset({'Monitor', 'Workflow'})
#: A ``task_updated`` status that means the job is over.
FINISHED_STATUSES = frozenset({'completed', 'failed', 'killed', 'stopped', 'cancelled', 'error'})

_NOTIFICATION_PREFIX = '<task-notification>'
_TOOL_USE_ID = re.compile(r'<tool-use-id>\s*([^<\s]+)\s*</tool-use-id>')


class BackgroundJobs(object):
    """The jobs still running, kept up to date one event at a time."""

    def __init__(self) -> None:
        # tool_use id (or task id when none is known) -> task id ('' = not known yet)
        self._jobs: dict[str, str] = {}
        self._unnamed_count = 0

    @property
    def outstanding(self) -> frozenset[str]:
        return frozenset(self._jobs)

    def observe(self, raw: object) -> None:
        if not isinstance(raw, dict):
            return
        kind = raw.get('type')
        if kind == 'assistant':
            for block in _tool_uses(raw):
                if _starts_background_work(block):
                    self._jobs.setdefault(str(block.get('id') or '') or self._unnamed(), '')
        elif kind == 'system':
            self._observe_system(raw)
        elif kind == 'user':
            self._observe_notification_text(raw)
        self._jobs.pop('', None)

    def _unnamed(self) -> str:
        """A key for a call that carries no id: still followed, never dropped."""
        self._unnamed_count += 1
        return f'unnamed-tool-use-{self._unnamed_count}'

    def _observe_system(self, raw: dict) -> None:
        subtype = raw.get('subtype')
        task_id = str(raw.get('task_id') or '')
        tool_use_id = str(raw.get('tool_use_id') or '')
        if subtype == 'task_started':
            key = tool_use_id or task_id
            if raw.get('is_backgrounded') is True or key in self._jobs:
                self._jobs[key] = task_id
        elif subtype == 'task_notification':
            self._finish(tool_use_id=tool_use_id, task_id=task_id)
        elif subtype == 'task_updated':
            patch = raw.get('patch')
            status = str(patch.get('status') or '') if isinstance(patch, dict) else ''
            if status in FINISHED_STATUSES:
                self._finish(task_id=task_id)
        elif subtype == 'background_tasks_changed':
            self._take_snapshot(raw.get('tasks'))

    def _take_snapshot(self, tasks: object) -> None:
        """The CLI's own list: a job it knows the task of and no longer lists
        is over; one it lists that is not followed yet is followed now."""
        running = {
            str(task.get('task_id') or '') for task in tasks or [] if isinstance(task, dict)
        } if isinstance(tasks, list) else set()
        running.discard('')
        for key, task_id in list(self._jobs.items()):
            if task_id and task_id not in running:
                del self._jobs[key]
        followed = set(self._jobs.values())
        for task_id in running - followed:
            self._jobs[task_id] = task_id

    def _observe_notification_text(self, raw: dict) -> None:
        text = _message_text(raw).lstrip()
        if not text.startswith(_NOTIFICATION_PREFIX):
            return
        named = _TOOL_USE_ID.search(text)
        if named:
            self._finish(tool_use_id=named.group(1))
        else:
            self._jobs.clear()  # an older notification that names nothing: all reported

    def _finish(self, *, tool_use_id: str = '', task_id: str = '') -> None:
        for key, known_task in list(self._jobs.items()):
            if (tool_use_id and key == tool_use_id) or (task_id and task_id in (key, known_task)):
                del self._jobs[key]


def outstanding_background_jobs(raw_events: Iterable[object]) -> frozenset[str]:
    """The jobs still running after ``raw_events`` (oldest first)."""
    jobs = BackgroundJobs()
    for raw in raw_events:
        jobs.observe(raw)
    return jobs.outstanding


def _tool_uses(raw: dict) -> list[dict]:
    message = raw.get('message')
    content = message.get('content') if isinstance(message, dict) else None
    if not isinstance(content, list):
        return []
    return [block for block in content if isinstance(block, dict) and block.get('type') == 'tool_use']


def _starts_background_work(block: dict) -> bool:
    if str(block.get('name') or '') in BACKGROUND_TOOLS:
        return True
    tool_input = block.get('input')
    return isinstance(tool_input, dict) and tool_input.get('run_in_background') is True


def _message_text(raw: dict) -> str:
    message = raw.get('message')
    content = message.get('content') if isinstance(message, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return ''.join(
            str(block.get('text') or '') for block in content
            if isinstance(block, dict) and block.get('type') == 'text'
        )
    return ''
