"""Shared base classes for provider HTTP clients.

Every provider sibling that talks to an external service
(GitHub, GitLab, Bitbucket, Jira, YouTrack, OpenHands, OpenRouter)
extends one of these.

Public surface:
    RetryingClientBase    - HTTP base + retry / Bearer-auth conventions
    PullRequestClientBase - ABC for pull-request clients
                            (extends RetryingClientBase)
"""

from provider_client_base.provider_client_base.retrying_client_base import (  # noqa: F401
    RetryingClientBase,
)
from provider_client_base.provider_client_base.pull_request_client_base import (  # noqa: F401
    PullRequestClientBase,
)
