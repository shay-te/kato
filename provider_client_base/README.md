# provider-client-base

Shared HTTP client base classes for the provider sibling
core-libs (GitHub, GitLab, Bitbucket, Jira, YouTrack, OpenHands,
OpenRouter). Lives in its own package so siblings don't reach
into the host for their parent class: siblings depend on this,
and the host consumes the siblings.

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
│   ├── subtask.py
│   └── fields.py
├── helpers/
│   ├── mention_utils.py          ← @-mention extraction + bot identity
│   ├── retry_utils.py
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

## Dependencies

Stdlib, `core_lib`'s `ClientBase` (the HTTP base `RetryingClientBase`
extends) and [`utils_core_lib`](../utils_core_lib/) for text helpers. No
other core-lib, and nothing from the host: product text such as a bot's
own reply prefixes reaches a client as a parameter.

## Tests

```
provider_client_base/provider_client_base/tests/
```
