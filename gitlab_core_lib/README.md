# gitlab-core-lib

GitLab provider implementation. Plays two roles:

- **Merge-request client** — plugs into
  [`repository_core_lib`](../repository_core_lib/) for create-MR /
  list-review-comments / reply / resolve-thread (kato treats GitLab
  MRs as PRs in the abstraction layer).
- **Issues client** — plugs into
  [`task_core_lib`](../task_core_lib/) as the
  `Platform.GITLAB_ISSUES` provider.

## Public API

```python
from gitlab_core_lib.gitlab_core_lib.gitlab_core_lib import GitLabCoreLib

gl = GitLabCoreLib(cfg)
gl.pull_request.create(repository, branch, title, body)
gl.issue.get_assigned_tasks(assignee='kato', states=['opened'])
```

## Config

```yaml
core_lib:
  gitlab_core_lib:
    base_url: https://gitlab.com/api/v4
    token: ${oc.env:GITLAB_API_TOKEN}
    project: your-org/your-repo
    max_retries: 3
```

Default schema in [`gitlab_core_lib/gitlab_core_lib/config/`](gitlab_core_lib/gitlab_core_lib/config/).

## Where this fits

Selected when the inventory's repo provider is GitLab OR when
`KATO_ISSUE_PLATFORM=gitlab_issues`.

## Tests

```
gitlab_core_lib/gitlab_core_lib/tests/
```
