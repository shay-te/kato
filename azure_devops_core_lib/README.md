# azure-devops-core-lib

Azure DevOps (Azure Repos) provider implementation — the **pull-request
client** that plugs into [`repository_core_lib`](../repository_core_lib/):
create a pull request, find one by branch, list its review comments, reply,
resolve the thread. Azure Boards (work items as tasks) is not provided.

## Public API

```python
from azure_devops_core_lib.azure_devops_core_lib.azure_devops_core_lib import AzureDevOpsCoreLib

azure = AzureDevOpsCoreLib(cfg)
azure.pull_request.create_pull_request('Fix login', 'feature/login', 'acme/Web App', 'api')
```

## Repository identity

Azure names a repository by organization (on-prem: collection), project and
repository. The client takes:

| argument | Azure DevOps Services | Azure DevOps Server (on-prem) |
|---|---|---|
| `base_url` | `https://dev.azure.com` | `https://server[:port]` |
| `repo_owner` | `<org>/<project>` | `<collection>/<project>` (or `tfs/<collection>/<project>`) |
| `repo_slug` | the repository name | the repository name |

Every request is `{base_url}/{owner}/_apis/git/repositories/{slug}/...?api-version=7.1`,
each path segment quoted (a project can be `Web App`). Legacy
`<org>.visualstudio.com` organizations are reached the same way through
`dev.azure.com`.

## Behaviour worth knowing

- **Auth** is a personal access token sent as HTTP Basic with an empty user
  name (`base64(":" + PAT)`), not Bearer.
- **Comment ids are `<thread>-<comment>`.** Azure numbers comments per thread
  (each thread starts at 1), so the bare number is not unique on a pull
  request. Replies and resolves need the thread id, carried on each comment as
  its resolution target.
- **Threads** that are `fixed` / `wontFix` / `closed` / `byDesign` are left
  out (resolved), as are deleted threads and comments and `system` comments
  (votes, pushes).
- **@-mentions** are stored by Azure as `@<GUID>`. They are rewritten to
  `@{uniqueName}` — resolved from the commenters, the pull request's author and
  reviewers, and the token's own account (`connectionData`) — else to
  `@{guid}`, so a mention filter that reads the brace form sees every mention.
- **Descriptions** over Azure's 4000-character limit are truncated (with a
  marker) instead of having the whole pull request refused.
- **Pull request URLs** returned are the web pages
  (`<repository webUrl>/pullrequest/<id>`), not the REST resources.

## Config

```yaml
core_lib:
  azure_devops_core_lib:
    base_url: https://dev.azure.com
    token: ${oc.env:AZURE_API_TOKEN}
    username: bot@example.com   # the bot's sign-in name, for @-mention matching
    max_retries: 3
```

Default schema in [`azure_devops_core_lib/config/`](azure_devops_core_lib/config/).

## Assumptions

Payload shapes follow the Azure DevOps REST API 7.1 reference: pull requests
(`pullRequestId`, `repository.webUrl`, `sourceRefName`, `createdBy`,
`reviewers`), threads (`status`, `isDeleted`, `threadContext.filePath`,
`rightFileStart.line` / `leftFileStart.line`, `comments[].commentType`), and
`_apis/connectionData` (`authenticatedUser.id`, `properties.Account.$value`).

## Tests

```
azure_devops_core_lib/azure_devops_core_lib/tests/
```
