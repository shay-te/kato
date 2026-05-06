# provider-client-base

Shared HTTP client base classes for the provider sibling
core-libs (GitHub, GitLab, Bitbucket, Jira, YouTrack, OpenHands,
OpenRouter). Lives in its own package so siblings don't reach
into `kato_core_lib` for their parent class — the dependency
arrow now points the right way (siblings depend on this; kato
consumes the siblings).

## What lives here

```
provider_client_base/provider_client_base/
├── retrying_client_base.py       ← HTTP base + retry / Bearer-auth conventions
└── pull_request_client_base.py   ← ABC for pull-request clients
                                     (extends RetryingClientBase)
```

## Public surface

```python
from provider_client_base.provider_client_base import (
    RetryingClientBase,
    PullRequestClientBase,
)
```

## What is NOT here (and why)

- **`TicketClientBase`** stays in `kato_core_lib` for now. It
  carries hardcoded kato-identity strings (e.g.
  `'Kato completed task '`, `'Kato agent could not safely process'`)
  used to de-dupe kato's own past comments on remote review threads.
  Moving it without a string-cleanup would leak kato identity into
  a neutral package. Tracked as future work — needs the kato-string
  filter logic to move into kato itself, leaving `TicketClientBase`
  with only generic ticket-platform plumbing.

## Temporary `kato_core_lib` dependency

These bases currently import a small set of helpers + DTOs from
`kato_core_lib`:

- `kato_core_lib.helpers.retry_utils` — retry wrapper
- `kato_core_lib.helpers.logging_utils` — logger config
- `kato_core_lib.data_layers.data.review_comment.ReviewComment`
- `kato_core_lib.data_layers.data.fields` — field-name constants

This is a temporary residual. The right cleanup is either pushing
the helpers upstream into the `core_lib` package and unifying
`ReviewComment` with `vcs_provider_contracts.ReviewComment`, or
moving those small pieces into this package. **The boundary win
today is that *siblings* no longer reach into kato — they reach
into this package.** That cuts the wrong-direction dep down to one
hop instead of six.

## Tests

```
provider_client_base/provider_client_base/tests/
```
