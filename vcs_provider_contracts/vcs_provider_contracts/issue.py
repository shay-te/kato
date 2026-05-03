from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Issue(object):
    id: str = ''
    title: str = ''
    body: str = ''
    state: str = ''
    labels: tuple[str, ...] = field(default_factory=tuple)
