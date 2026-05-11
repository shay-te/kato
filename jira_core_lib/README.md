# jira-core-lib

Jira ticket-platform implementation. Plugs into
[`task_core_lib`](../task_core_lib/) as the `Platform.JIRA`
provider.

## Public API

```python
from jira_core_lib.jira_core_lib.jira_core_lib import JiraCoreLib

jira = JiraCoreLib(cfg).issue
jira.get_assigned_tasks(assignee='kato', states=['To Do', 'In Progress'])
jira.add_comment(task_id='PROJ-123', body='…')
jira.move_task_to_state(task_id='PROJ-123', state='In Review')
```

## Config

Driven by Hydra under `core_lib.jira_core_lib`:

```yaml
core_lib:
  jira_core_lib:
    base_url: https://your-org.atlassian.net
    email: ${oc.env:JIRA_API_EMAIL}
    token: ${oc.env:JIRA_API_TOKEN}
    max_retries: 3
```

Default schema in [`jira_core_lib/config/`](jira_core_lib/config/).

## Where this fits

Selected by kato when `KATO_ISSUE_PLATFORM=jira`. Routed through
`task_core_lib`'s `TaskClientFactory`.

## Tests

```
jira_core_lib/jira_core_lib/tests/
```
