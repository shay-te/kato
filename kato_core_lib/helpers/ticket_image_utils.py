"""A ticket's screenshots, saved into its task folder for the agent to open.

The tracker hands an image out as a link the agent cannot use — YouTrack's is
host-less and signed (``/api/files/8-1437?sign=…``), Jira's needs kato's
credentials — so the agent can only see a screenshot kato saved for it.

ONE helper, because every way an agent starts on a ticket needs them: the
autonomous pickup (preflight), a hold chat (``kato:wait-planning`` /
``kato:wait-editing``) and the first turn of a fresh chat. Only the autonomous
pickup used to fetch them, so a chat was handed the tracker's link and the
operator pasted every screenshot in by hand (UNA-3242).
"""

from __future__ import annotations

import os

from agent_core_lib.agent_core_lib.helpers.agent_prompt_utils import (
    task_attachments_directory,
)

from kato_core_lib.helpers.mission_logging_utils import log_mission_step


def download_ticket_images(task_service, task_id: str, workspace_root: str, logger) -> list[str]:
    """The ticket's images, saved into ``<workspace_root>/attachments``.

    Returns the local paths, for the prompt to name. Safe to call on every
    start: a copy an earlier call saved is reused, never duplicated.

    Only into a task folder that already EXISTS — a missing one means there is
    no workspace to put files in, and creating one here would leave a stray
    task folder behind. Best-effort, always: an image kato cannot fetch costs
    the agent some context, while raising would cost the operator the task.
    """
    folder = str(workspace_root or '').strip()
    if task_service is None or not folder or not os.path.isdir(folder):
        return []
    try:
        paths = list(
            task_service.download_image_attachments(
                task_id, task_attachments_directory(folder),
            ) or []
        )
    except Exception:
        logger.exception('failed to download ticket attachments for task %s', task_id)
        return []
    if paths:
        log_mission_step(logger, task_id, 'downloaded %d ticket image(s)', len(paths))
    return paths
