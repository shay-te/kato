"""A task's Docker containers: the ones its agent started in the task's folder.

An agent runs ``docker compose up`` or ``docker run`` like any developer, and
nothing stopped them when the task ended: a finished task kept its database and
its dev server running. Kato knows a task's containers by its folder
(``container_core_lib``: a Compose project there, or a mount from it), so:

* the operator sees them on the task's header and stops or removes them;
* a task reaching Done has them STOPPED — started again with one command,
  nothing deleted (``DoneAnnouncements`` tells this observer);
* a task deleted has them REMOVED, with what Compose made for them — the
  folder they ran from is going too (the delete route).

Never when the agent merely stops or a turn ends: a dev server the operator is
still using must not vanish under them. Kato's own sandbox containers are left
to the sandbox, which removes its own.
"""

from __future__ import annotations

import threading

from container_core_lib.container_core_lib import DockerCli, DockerUnavailable, FolderContainers, Outcome
from sandbox_core_lib.sandbox_core_lib.manager import SANDBOX_CONTAINER_LABEL

from kato_core_lib.helpers.automation_switches_utils import stop_containers_on_done
from kato_core_lib.helpers.late_binding import provider_for
from kato_core_lib.helpers.logging_utils import configure_logger
from kato_core_lib.helpers.workspace_repo_utils import task_workspace_root


def _in_background(work) -> None:
    threading.Thread(target=work, name='task-containers', daemon=True).start()


class TaskContainers(object):
    def __init__(self, *, workspace_manager, containers=None, run_later=_in_background, logger=None) -> None:
        self._get_workspace_manager = provider_for(workspace_manager)
        self._containers = containers or FolderContainers(DockerCli(), ignore_label=SANDBOX_CONTAINER_LABEL)
        self._run_later = run_later
        self.logger = logger or configure_logger('TaskContainers')

    def of_task(self, task_id: str) -> dict:
        """``{available, containers, reason}`` — ``available`` False (with
        Docker's reason) when Docker cannot be asked."""
        folder = self._folder(task_id)
        if not folder:
            return {'available': True, 'containers': [], 'reason': ''}
        try:
            found = self._containers.in_folder(folder)
        except DockerUnavailable as exc:
            return {'available': False, 'containers': [], 'reason': str(exc)}
        return {'available': True, 'containers': [container.to_dict() for container in found], 'reason': ''}

    def stop(self, task_id: str) -> Outcome:
        folder = self._folder(task_id)
        return self._containers.stop(folder) if folder else Outcome()

    def remove(self, task_id: str) -> Outcome:
        folder = self._folder(task_id)
        return self._containers.remove(folder) if folder else Outcome()

    def task_done(self, task_id: str) -> None:
        """A task reached Done: its running containers are stopped, off the
        done's own path (Docker can take seconds per container) — unless the
        operator switched that off (Settings → Automation, read fresh)."""
        if not stop_containers_on_done():
            return
        self._run_later(lambda: self._stop_after_done(task_id))

    def _stop_after_done(self, task_id: str) -> None:
        listed = self.of_task(task_id)
        if not any(container['running'] for container in listed['containers']):
            return  # nothing running, or no Docker here: nothing to say
        outcome = self.stop(task_id)
        if outcome.containers:
            self.logger.info('task %s is done: stopped its containers %s', task_id, ', '.join(outcome.containers))
        for problem in outcome.problems:
            self.logger.warning('task %s is done, but a container could not be stopped: %s', task_id, problem)

    def _folder(self, task_id: str) -> str:
        try:
            return task_workspace_root(self._get_workspace_manager(), task_id)
        except Exception:
            return ''
