from __future__ import annotations

from enum import Enum
from urllib.parse import urlparse

from git_core_lib.git_core_lib.helpers.repository_discovery_utils import (
    is_azure_devops_host,
)


class Platform(Enum):
    """Repository platforms supported by repository_core_lib."""

    GITHUB = 'github'
    GITLAB = 'gitlab'
    BITBUCKET = 'bitbucket'
    AZURE = 'azure'

    @classmethod
    def from_base_url(cls, base_url: str) -> Platform:
        parsed = urlparse(base_url)
        # By HOST, and first: the substring checks below also read the path,
        # so an Azure base like ``https://dev.azure.com/acme-github`` must not
        # land on them. An on-prem Azure DevOps Server's host is its own and
        # cannot be recognised here — the caller passes ``azure`` explicitly.
        if is_azure_devops_host(parsed.hostname or ''):
            return cls.AZURE
        target = f'{parsed.netloc}{parsed.path}'.lower()
        if 'github' in target:
            return cls.GITHUB
        if 'gitlab' in target:
            return cls.GITLAB
        if 'bitbucket' in target:
            return cls.BITBUCKET
        raise ValueError(f'unsupported repository provider for base_url: {base_url}')
