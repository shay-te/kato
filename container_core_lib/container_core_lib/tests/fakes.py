"""A Docker that answers the command line from memory."""

from __future__ import annotations

import json

from container_core_lib.container_core_lib.docker_cli import Answer, DockerCli


def raw_container(cid: str, name: str, *, state: str = 'running', image: str = 'postgres:16',
                  labels: dict | None = None, binds: tuple[str, ...] = (), volumes: tuple[str, ...] = ()) -> dict:
    mounts = [{'Type': 'bind', 'Source': source, 'Destination': '/app'} for source in binds]
    mounts += [{'Type': 'volume', 'Source': f'/var/lib/docker/volumes/{v}/_data', 'Name': v} for v in volumes]
    return {
        'Id': cid * 4, 'Name': f'/{name}', 'Config': {'Image': image, 'Labels': labels or {}},
        'State': {'Status': state}, 'Mounts': mounts,
    }


def compose_labels(project: str, folder: str, files: str = '') -> dict:
    return {
        'com.docker.compose.project': project,
        'com.docker.compose.project.working_dir': folder,
        'com.docker.compose.project.config_files': files or f'{folder}/docker-compose.yaml',
    }


class FakeDocker(object):
    """``run`` for ``DockerCli``: ps / inspect / stop / rm / network / volume."""

    def __init__(self) -> None:
        self.containers: dict[str, dict] = {}
        self.networks: dict[str, str] = {}     # id -> its compose project
        self.volumes: dict[str, str] = {}      # name -> its compose project
        self.refuse: dict[str, str] = {}       # id or name -> Docker's words
        self.down = ''                         # the engine's answer when it is not running
        self.calls: list[list[str]] = []
        self.vanish_before_inspect: set[str] = set()

    def add(self, raw: dict) -> None:
        self.containers[raw['Id']] = raw

    def cli(self) -> DockerCli:
        return DockerCli('docker', run=self)

    def __call__(self, argv: list[str], timeout: float) -> Answer:
        self.calls.append(argv[1:])
        if self.down:
            return Answer(1, '', self.down)
        command, *args = argv[1:]
        handler = getattr(self, f'_{command}')
        return handler(args)

    def _ps(self, args):
        return Answer(0, ''.join(f'{cid}\n' for cid in self.containers))

    def _inspect(self, args):
        for cid in self.vanish_before_inspect:
            self.containers.pop(cid, None)
        found = [self.containers[cid] for cid in args if cid in self.containers]
        missing = [cid for cid in args if cid not in self.containers]
        return Answer(1 if missing else 0, json.dumps(found), ''.join(f'Error: No such object: {m}\n' for m in missing))

    def _full(self, short: str) -> str:
        return next((cid for cid in self.containers if cid.startswith(short)), short)

    def _stop(self, args):
        cid = self._full(args[0])
        if cid in self.refuse:
            return Answer(1, '', self.refuse[cid])
        self.containers[cid]['State']['Status'] = 'exited'
        return Answer(0, args[0])

    def _rm(self, args):
        cid = self._full(args[-1])
        if cid in self.refuse:
            return Answer(1, '', self.refuse[cid])
        self.containers.pop(cid)
        return Answer(0, args[-1])

    def _network(self, args):
        return self._resource(self.networks, args)

    def _volume(self, args):
        return self._resource(self.volumes, args)

    def _resource(self, store: dict, args):
        if args[0] == 'ls':
            project = args[-1].rsplit('=', 1)[1]
            return Answer(0, ''.join(f'{key}\n' for key, owner in store.items() if owner == project))
        name = args[1]
        if name in self.refuse:
            return Answer(1, '', self.refuse[name])
        store.pop(name)
        return Answer(0, name)
