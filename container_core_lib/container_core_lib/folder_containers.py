"""The containers that belong to a folder: find them, stop them, remove them.

A container belongs to a folder when:

* its Compose project lives there (the project's folder, or one of its
  compose files, is in it), or
* it mounts anything from the folder (a ``docker run -v $PWD:/app`` dev server,
  a database whose data sits in the folder).

A container carrying ``ignore_label`` is never one of them: the host's own
containers (a sandbox it runs agents in) mount the folder too, and look after
themselves.

Removing goes as far as Compose's own ``down --volumes``: the containers with
their anonymous volumes, then — for a project none of whose containers is left
anywhere — the networks and volumes Compose made for it. A project still
running elsewhere keeps them.
"""

from __future__ import annotations

import os

from container_core_lib.container_core_lib.data import Container, Outcome
from container_core_lib.container_core_lib.errors import DockerUnavailable
from container_core_lib.container_core_lib.paths import is_inside

COMPOSE_PROJECT = 'com.docker.compose.project'
COMPOSE_WORKING_DIR = 'com.docker.compose.project.working_dir'
COMPOSE_CONFIG_FILES = 'com.docker.compose.project.config_files'


class FolderContainers(object):
    def __init__(self, docker, *, ignore_label: str = '') -> None:
        self._docker = docker
        self._ignore_label = ignore_label

    def in_folder(self, folder: str) -> list[Container]:
        """The folder's containers, running first. Raises ``DockerUnavailable``."""
        places = _places(folder)
        found = [_container(raw) for raw in self._docker.containers() if self._belongs(raw, places)]
        return sorted(found, key=lambda container: (not container.running, container.name))

    def stop(self, folder: str) -> Outcome:
        """Stop the folder's running containers (they can be started again)."""
        try:
            running = [container for container in self.in_folder(folder) if container.running]
        except DockerUnavailable as exc:
            return Outcome(problems=(str(exc),))
        stopped, problems = [], []
        for container in running:
            problem = self._docker.stop(container.id)
            if problem:
                problems.append(f'{container.name}: {problem}')
            else:
                stopped.append(container.name)
        return Outcome(containers=tuple(stopped), problems=tuple(problems))

    def remove(self, folder: str) -> Outcome:
        """Remove the folder's containers, and what Compose made for a project
        that has none left."""
        try:
            containers = self.in_folder(folder)
        except DockerUnavailable as exc:
            return Outcome(problems=(str(exc),))
        removed, problems = [], []
        for container in containers:
            problem = self._docker.remove(container.id)
            if problem:
                problems.append(f'{container.name}: {problem}')
            else:
                removed.append(container.name)
        projects = sorted({container.project for container in containers if container.project})
        networks, volumes = [], []
        if projects:
            try:
                self._remove_left_behind(projects, networks, volumes, problems)
            except DockerUnavailable as exc:
                problems.append(str(exc))
        return Outcome(tuple(removed), tuple(networks), tuple(volumes), tuple(problems))

    def _remove_left_behind(self, projects, networks: list, volumes: list, problems: list) -> None:
        still_used = {_labels(raw).get(COMPOSE_PROJECT, '') for raw in self._docker.containers()}
        for project in projects:
            if project in still_used:
                continue  # a container of it is still there: it still needs them
            for network in self._docker.project_networks(project):
                _note(self._docker.remove_network(network), network, networks, problems)
            for volume in self._docker.project_volumes(project):
                _note(self._docker.remove_volume(volume), volume, volumes, problems)

    def _belongs(self, raw: dict, places: list[str]) -> bool:
        labels = _labels(raw)
        if self._ignore_label and self._ignore_label in labels:
            return False
        paths = [labels.get(COMPOSE_WORKING_DIR, '')]
        paths += labels.get(COMPOSE_CONFIG_FILES, '').split(',')
        paths += [str(mount.get('Source') or '') for mount in raw.get('Mounts') or [] if isinstance(mount, dict)]
        return any(is_inside(path, place) for path in paths if path.strip() for place in places)


def _places(folder: str) -> list[str]:
    """The folder as given and as it really is: a mount names it the way the
    command that made it did, through a link or not."""
    given = str(folder or '').strip()
    if not given:
        return []
    real = os.path.realpath(given)
    return [given] if real == given else [given, real]


def _labels(raw: dict) -> dict:
    config = raw.get('Config') if isinstance(raw.get('Config'), dict) else {}
    labels = config.get('Labels')
    return labels if isinstance(labels, dict) else {}


def _container(raw: dict) -> Container:
    config = raw.get('Config') if isinstance(raw.get('Config'), dict) else {}
    state = raw.get('State') if isinstance(raw.get('State'), dict) else {}
    return Container(
        id=str(raw.get('Id') or '')[:12],
        name=str(raw.get('Name') or '').lstrip('/'),
        image=str(config.get('Image') or ''),
        state=str(state.get('Status') or ''),
        project=str(_labels(raw).get(COMPOSE_PROJECT) or ''),
    )


def _note(problem: str, name: str, done: list, problems: list) -> None:
    if problem:
        problems.append(f'{name}: {problem}')
    else:
        done.append(name)
