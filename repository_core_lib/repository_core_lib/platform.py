from __future__ import annotations

from enum import Enum
from urllib.parse import urlparse


class Platform(Enum):
    """Repository platforms supported by repository_core_lib."""

    GITHUB = 'github'
    GITLAB = 'gitlab'
    BITBUCKET = 'bitbucket'

    @classmethod
    def from_base_url(cls, base_url: str) -> Platform:
        parsed = urlparse(base_url)
        target = f'{parsed.netloc}{parsed.path}'.lower()
        if 'github' in target:
            return cls.GITHUB
        if 'gitlab' in target:
            return cls.GITLAB
        if 'bitbucket' in target:
            return cls.BITBUCKET
        raise ValueError(f'unsupported repository provider for base_url: {base_url}')
