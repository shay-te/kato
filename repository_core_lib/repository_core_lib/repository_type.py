from __future__ import annotations

from enum import Enum
from urllib.parse import urlparse


class RepositoryType(str, Enum):
    GITHUB = 'github'
    GITLAB = 'gitlab'
    BITBUCKET = 'bitbucket'

    @classmethod
    def from_base_url(cls, base_url: str) -> RepositoryType:
        parsed = urlparse(base_url)
        target = f'{parsed.netloc}{parsed.path}'.lower()
        if 'github' in target:
            return cls.GITHUB
        if 'gitlab' in target:
            return cls.GITLAB
        if 'bitbucket' in target:
            return cls.BITBUCKET
        raise ValueError(f'unsupported repository provider for base_url: {base_url}')
