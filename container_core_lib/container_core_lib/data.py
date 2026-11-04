"""A container as the caller needs it, and what an action did."""

from __future__ import annotations

from dataclasses import dataclass

#: States in which a container still runs something (``docker stop`` ends them).
RUNNING_STATES = frozenset({'running', 'restarting', 'paused'})


@dataclass(frozen=True)
class Container(object):
    id: str                       # Docker's short id
    name: str
    image: str
    state: str                    # running | exited | created | paused | restarting | dead
    project: str = ''             # its Compose project ('' = started on its own)

    @property
    def running(self) -> bool:
        return self.state in RUNNING_STATES

    def to_dict(self) -> dict:
        return {
            'id': self.id, 'name': self.name, 'image': self.image,
            'state': self.state, 'project': self.project, 'running': self.running,
        }


@dataclass(frozen=True)
class Outcome(object):
    """What a stop or a removal did: the containers (by name) it acted on,
    the networks and volumes it removed, and what failed, in Docker's words."""

    containers: tuple[str, ...] = ()
    networks: tuple[str, ...] = ()
    volumes: tuple[str, ...] = ()
    problems: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.problems

    def to_dict(self) -> dict:
        return {
            'containers': list(self.containers), 'networks': list(self.networks),
            'volumes': list(self.volumes), 'problems': list(self.problems), 'ok': self.ok,
        }
