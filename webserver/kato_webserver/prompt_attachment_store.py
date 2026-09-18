"""Large-file composer attachments, saved into the task workspace.

Small text files are inlined into the prompt as a fenced block — cheap, and
the agent sees the content directly. That stops working as the file grows: a
multi-megabyte log inlined into a prompt is mostly wasted context, and the UI
used to silently truncate it, so the operator handed over a log whose
interesting part had been cut off with only a toast to say so.

Past a size threshold the file is written HERE instead and the prompt carries
its path. The agent then reads or greps it with its own tools, at whatever
granularity the question needs, with nothing truncated.

Files land in ``<workspace>/attachments/``. That is the task folder — the
clone's PARENT, and the agent's ``--add-dir`` scope — so:

* the agent can read them, and
* git cannot stage them, because they are outside every worktree. The same
  reasoning puts ``pr_description.md`` there (see
  ``RepositoryService._pr_description_from_task_folder``); an attachment
  dropped inside a clone would otherwise land in the operator's next commit.

Names are sanitised to a bare filename before use: an upload is operator
input, and ``../`` in a name would otherwise write anywhere on disk.
"""
from __future__ import annotations

from pathlib import Path

from agent_core_lib.agent_core_lib.helpers.agent_prompt_utils import (
    TASK_ATTACHMENTS_DIRNAME,
    task_attachments_directory,
)
from kato_core_lib.helpers.logging_utils import configure_logger
from utils_core_lib.utils_core_lib.filename_utils import (
    safe_attachment_name,
    unique_file_path,
)

#: The task-folder convention, defined once in ``agent_prompt_utils`` because
#: ticket attachments land in the same folder and the agent's prompt names it.
ATTACHMENTS_DIRNAME = TASK_ATTACHMENTS_DIRNAME

# Enough for the logs and dumps operators actually hand over, while still
# refusing something that would fill the disk.
MAX_ATTACHMENT_BYTES = 64 * 1024 * 1024

_logger = configure_logger('PromptAttachmentStore')

def attachments_dir(workspace_dir) -> Path:
    return Path(task_attachments_directory(workspace_dir))


def save_attachment(workspace_dir, name: str, data: bytes) -> dict:
    """Write ``data`` into the task's attachments folder.

    Returns ``{'ok': True, 'name', 'path', 'bytes'}`` on success, or
    ``{'ok': False, 'error'}`` — never raises, so a failed attachment leaves
    the composer usable.
    """
    if not workspace_dir:
        return {'ok': False, 'error': 'no workspace for this task'}
    payload = data or b''
    if len(payload) > MAX_ATTACHMENT_BYTES:
        megabytes = MAX_ATTACHMENT_BYTES // (1024 * 1024)
        return {'ok': False, 'error': f'file is larger than {megabytes} MB'}
    if not payload:
        return {'ok': False, 'error': 'file is empty'}
    directory = attachments_dir(workspace_dir)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        target = unique_file_path(directory, safe_attachment_name(name))
        target.write_bytes(payload)
    except Exception as error:  # noqa: BLE001 - surfaced to the operator
        _logger.exception('failed to save attachment %s', name)
        return {'ok': False, 'error': str(error) or 'could not save the file'}
    return {
        'ok': True,
        'name': target.name,
        'path': str(target),
        'bytes': len(payload),
    }
