# github-core-lib

GitHub provider implementation. Plays two roles:

- **Pull-request client** — plugs into
  [`repository_core_lib`](../repository_core_lib/) for create-PR /
  list-review-comments / reply / resolve-thread.
- **Issues client** — plugs into
  [`task_core_lib`](../task_core_lib/) as the
  `Platform.GITHUB_ISSUES` provider.

## Public API

```python
from github_core_lib.github_core_lib.github_core_lib import GitHubCoreLib

gh = GitHubCoreLib(cfg)
gh.pull_request.create(repository, branch, title, body)
gh.pull_request.list_review_comments(repository, pr_id)
gh.issue.get_assigned_tasks(assignee='kato', states=['open'])
```

## Config

```yaml
core_lib:
  github_core_lib:
    base_url: https://api.github.com
    token: ${oc.env:GITHUB_API_TOKEN}
    owner: your-org
    repo: your-repo
    max_retries: 3
```

Default schema in [`github_core_lib/github_core_lib/config/`](github_core_lib/github_core_lib/config/).

## Where this fits

Selected when the inventory's repo provider is GitHub OR when
`KATO_ISSUE_PLATFORM=github_issues`.

## Tests

```
github_core_lib/github_core_lib/tests/
```
