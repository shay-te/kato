# bitbucket-core-lib

Bitbucket provider implementation. Plays two roles:

- **Pull-request client** — plugs into
  [`repository_core_lib`](../repository_core_lib/) for create-PR /
  list-review-comments / reply / resolve-thread.
- **Issues client** — plugs into
  [`task_core_lib`](../task_core_lib/) as the
  `Platform.BITBUCKET_ISSUES` provider.

## Public API

```python
from bitbucket_core_lib.bitbucket_core_lib.bitbucket_core_lib import (
    BitbucketCoreLib,
)

bb = BitbucketCoreLib(cfg)
bb.pull_request.create(repository, branch, title, body)
bb.pull_request.list_review_comments(repository, pr_id)
bb.issue.get_assigned_tasks(assignee='kato', states=['new', 'open'])
```

## Config

```yaml
core_lib:
  bitbucket_core_lib:
    base_url: https://api.bitbucket.org/2.0
    token: ${oc.env:BITBUCKET_API_TOKEN}
    api_email: ${oc.env:BITBUCKET_API_EMAIL}
    workspace: your-workspace
    repo_slug: your-repo
    max_retries: 3
```

Default schema in [`bitbucket_core_lib/bitbucket_core_lib/config/`](bitbucket_core_lib/bitbucket_core_lib/config/).

## Where this fits

Selected when the inventory's repo provider is Bitbucket OR when
`KATO_ISSUE_PLATFORM=bitbucket_issues`.

## Tests

```
bitbucket_core_lib/bitbucket_core_lib/tests/
```
