"""Find the Docker containers that belong to a folder, then stop or remove them.

A container belongs to a folder when its Compose project lives there, or when
it mounts anything from it — so the containers an agent started while working
in a task's folder can be found, stopped and cleaned up after the task, with
no mark of their own.
"""

from container_core_lib.container_core_lib.data import Container, Outcome
from container_core_lib.container_core_lib.docker_cli import DockerCli
from container_core_lib.container_core_lib.errors import DockerUnavailable
from container_core_lib.container_core_lib.folder_containers import FolderContainers

__all__ = ['Container', 'DockerCli', 'DockerUnavailable', 'FolderContainers', 'Outcome']
