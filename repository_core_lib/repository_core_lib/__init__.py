"""Repository core-lib.

Routes pull-request operations to a configured provider
(GitHub, GitLab, Bitbucket).  Provider client packages are resolved
lazily — inject custom factory callables to decouple from them entirely.

Public surface:
    RepositoryCoreLib        - composition root (extends CoreLib)
    Platform                 - enum of supported repository providers
    PullRequestService       - service that routes PR ops to a provider client
    PullRequestClientFactory - factory that builds provider-specific clients
"""

from repository_core_lib.repository_core_lib.repository_core_lib import (  # noqa: F401
    RepositoryCoreLib,
)
from repository_core_lib.repository_core_lib.platform import Platform  # noqa: F401
from repository_core_lib.repository_core_lib.pull_request_service import (  # noqa: F401
    PullRequestService,
)
from repository_core_lib.repository_core_lib.client.pull_request_client_factory import (  # noqa: F401
    PullRequestClientFactory,
)
