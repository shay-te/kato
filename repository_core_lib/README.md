# repository-core-lib

Pull-request abstraction. Hides the choice of GitHub / GitLab /
Bitbucket behind a single `RepositoryCoreLib(cfg, max_retries).pull_request`
service.

## Public API

```python
from repository_core_lib.repository_core_lib.repository_core_lib import (
    RepositoryCoreLib,
)

pr = RepositoryCoreLib(cfg, max_retries=3).pull_request
pr.create(repository, branch_name, title, body)
pr.find_open(repository, branch_name)
pr.list_review_comments(repository, pull_request_id)
pr.reply(repository, pull_request_id, comment_id, body)
pr.resolve_thread(repository, pull_request_id, thread_id)
```

`PullRequestClientFactory` resolves the concrete client from the
config's provider field. New providers register there.

## Where this fits

Kato calls into this lib from
`kato_core_lib/data_layers/service/repository_service.py` whenever
it needs to push a branch to a remote, open a PR, sync review
comments, or close a thread.

Per-provider implementations:
[`github_core_lib`](../github_core_lib/) ·
[`gitlab_core_lib`](../gitlab_core_lib/) ·
[`bitbucket_core_lib`](../bitbucket_core_lib/).

## Tests

```
repository_core_lib/repository_core_lib/tests/
```
