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
├── pull_request_client_base.py   ← ABC for pull-request clients
│                                    (extends RetryingClientBase)
├── client/
│   └── issue_client_base.py      ← ABC every issue/ticket client extends;
│                                    owns the @-mention filter scaffold
├── data/
│   ├── review_comment.py         ← the shared ReviewComment type
│   ├── issue_record.py           ← the shared IssueRecord type
│   └── fields.py
├── helpers/
│   ├── mention_utils.py          ← @-mention extraction + bot identity
│   ├── retry_utils.py
│   ├── text_utils.py
│   └── logging_utils.py
└── testing.py
```

## Public surface

```python
from provider_client_base.provider_client_base import (
    RetryingClientBase,
    PullRequestClientBase,
)
```

## @-mention filtering (`helpers/mention_utils.py`)

A ticket/PR comment that `@`-tags a human is **that person's to answer**, not
the bot's. Every platform client applies the same rule, and it lives here so
it cannot be re-implemented per platform — which is exactly how it kept
regressing.

```python
from provider_client_base.provider_client_base.helpers.mention_utils import (
    extract_all_mention_tokens,   # plain @login ∪ brace @{account_id} / @{Full Name}
    mentions_include_identity,
    normalize_bot_identities,     # drop blanks + query aliases, lowercase, de-dupe
    BOT_IDENTITY_ALIASES,         # {'me', 'currentuser()'} — never real handles
)
```

Rules that are load-bearing — read before changing anything here:

- **Use `extract_all_mention_tokens`, never a narrower extractor.** A mention
  encoding the extractor does not recognise yields an empty list, and an empty
  list is indistinguishable from "this comment tags nobody" — so the filter
  **fails open** and the agent acts on a comment addressed to a human. Every
  recurrence of that bug came from one caller using a plain-`@login`-only
  extractor while the others had moved on.
- **Never build a bot-identity tuple by hand.** Use `normalize_bot_identities`.
  A tuple holding only a query alias such as `currentUser()` is *worse* than an
  empty one: identity-aware callers treat "non-empty" as "we know who the bot
  is" and then compare against a value that can never match.
- `IssueClientBase._extract_comment_mentions` is the per-platform hook. Override
  it only to ADD a wire format the shared extractor cannot see (Jira's ADF
  nodes); always union with the shared result rather than replacing it.

Cross-path agreement between the issue-comment and PR-review filters is
enforced by `tests/test_comment_mention_cross_path.py` in the host repo.

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
