"""Start a local task that was just created: its mode, then clone + chat.

The one way a local task is started — by the "New task" tab and by the plan
queue alike. The model / effort / mode are applied BEFORE the chat starts (the
CLI fixes ``--model``, ``--effort`` and ``--permission-mode`` when it spawns);
the slow part — clone the repositories, check out the branch, send the opening
message for the mode — runs in the background.
"""

from __future__ import annotations

import threading
from typing import Callable

from agent_core_lib.agent_core_lib.helpers.agent_prompt_utils import (
    task_uploaded_files_block,
)
from flask import Flask

from kato_core_lib.data_layers.service.local_task_service import (
    START_IMPLEMENT,
    START_PERMISSION_MODE,
    opening_message,
)
from kato_core_lib.helpers.mission_logging_utils import log_mission_step
from kato_core_lib.helpers.plan_mode_store import picked_mode_to_store
from kato_webserver.prompt_attachment_store import list_attachment_paths


class LocalTaskStarter(object):
    def __init__(
        self,
        app: Flask,
        *,
        set_mode: Callable[[str, str], bool],
        set_override: Callable[[str, str, str], bool],
        start_chat: Callable[[str, str], object],
        run_in_background: Callable[[Callable[[], None], str], None] | None = None,
    ) -> None:
        self._app = app
        self._set_mode = set_mode
        self._set_override = set_override
        self._start_chat = start_chat
        self._background = run_in_background or _daemon_thread

    def start(self, task_id: str, start_mode: str, *, model: str = '', effort: str = '') -> None:
        """Apply the mode and overrides now; prepare and start in the background.

        ``start_mode`` must be one of ``START_MODES`` (the caller validates).
        """
        # "Implement" is a pick: once a default mode for new tasks is set it
        # is kept explicitly, or a Plan default would make "Implement this
        # task" a planning turn. "Chat" picks nothing — the default applies.
        mode = START_PERMISSION_MODE[start_mode]
        if start_mode == START_IMPLEMENT:
            mode = picked_mode_to_store(mode, task_id)
        self._set_mode(task_id, mode)
        _persist_mode(task_id, mode)
        self._set_override('TASK_MODEL_OVERRIDES', task_id, str(model or '').strip())
        self._set_override('TASK_EFFORT_OVERRIDES', task_id, effort)
        self._background(lambda: self._prepare_and_start(task_id, start_mode), f'local-task-{task_id}')

    def _prepare_and_start(self, task_id: str, start_mode: str) -> None:
        service = getattr(self._app.config.get('AGENT_SERVICE'), 'local_tasks', None)
        try:
            ready = service.prepare(task_id)
        except Exception:
            self._app.logger.exception('preparing local task %s failed', task_id)
            ready = False
        if not ready:
            log_mission_step(
                self._app.logger, task_id,
                'the repositories could not be prepared — the chat was not started',
            )
            return
        message = opening_message(start_mode)
        if not message:
            return
        message = _with_attached_files(self._app, task_id, message)
        with self._app.app_context():
            self._start_chat(task_id, message)


def _with_attached_files(app: Flask, task_id: str, message: str) -> str:
    """Name files the operator attached when creating the task, ahead of the
    opener — "task creation itself acts as the first prompt", so a document
    handed over at creation (a spec, a CSV, a PDF) must be named in it, not
    discovered by the agent later by chance.

    The route that created the task saves them into the attachments folder
    BEFORE this background thread is even started, so reading the folder
    here — after the clone, right before the opener is sent — is never a
    race against the upload.
    """
    workspace_manager = app.config.get('WORKSPACE_MANAGER')
    if workspace_manager is None:
        return message
    try:
        workspace_dir = str(workspace_manager.workspace_path(task_id))
    except Exception:
        return message
    block = task_uploaded_files_block(list_attachment_paths(workspace_dir))
    return f'{block}\n\n{message}' if block else message


def _persist_mode(task_id: str, mode: str) -> None:
    # Best-effort, as the agent-mode route does: the in-memory pick already
    # governs this run's spawn; the file only carries it across a restart.
    from kato_core_lib.helpers.plan_mode_store import set_task_mode
    set_task_mode(task_id, mode)


def _daemon_thread(work: Callable[[], None], name: str) -> None:
    threading.Thread(target=work, name=name, daemon=True).start()
