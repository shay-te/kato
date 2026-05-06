"""Shared base classes for provider HTTP clients.

Every provider sibling that talks to an external service
(GitHub, GitLab, Bitbucket, Jira, YouTrack, OpenHands, OpenRouter)
extends one of these. Lives here so siblings don't have to reach
into ``kato_core_lib`` for their parent class — the dependency
direction stays right (siblings depend on this; kato consumes
through siblings).

Public surface:
    RetryingClientBase    - HTTP base + retry / Bearer-auth conventions
    PullRequestClientBase - ABC for pull-request clients
                            (extends RetryingClientBase)

Note: ``TicketClientBase`` (the issue-platform sibling base) is
NOT here yet — it carries kato-specific identity strings used to
de-duplicate kato's own past comments on remote threads. Moving
it without a string-cleanup would leak kato identity into a
neutral package. Tracked as future work.

Note: this package currently has a temporary dependency on
``kato_core_lib`` for the small set of helpers + DTOs the bases
need (``retry_utils``, ``logging_utils``, ``ReviewComment``,
field constants). Cleaning that up (either pushing the helpers
upstream into ``core_lib`` or unifying with
``vcs_provider_contracts``) is a separate refactor. The
boundary win today is that *siblings* no longer reach into kato
— they reach into this package.
"""

from provider_client_base.provider_client_base.retrying_client_base import (  # noqa: F401
    RetryingClientBase,
)
from provider_client_base.provider_client_base.pull_request_client_base import (  # noqa: F401
    PullRequestClientBase,
)
