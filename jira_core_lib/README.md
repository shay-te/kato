# jira-core-lib

Jira ticket-platform implementation. Plugs into
[`task_core_lib`](../task_core_lib/) as the `Platform.JIRA`
provider.

## Public API

```python
from jira_core_lib.jira_core_lib.jira_core_lib import JiraCoreLib

jira = JiraCoreLib(cfg).issue
jira.get_assigned_tasks('PROJ', 'bot-account', ['To Do', 'In Progress'])
jira.add_comment('PROJ-123', '…')
jira.move_issue_to_state('PROJ-123', 'status', 'In Review')

# Sub-tasks (supports_subtasks = True)
key = jira.create_subtask('PROJ-123', 'Summary', 'Description with a marker')  # unassigned
jira.find_subtask('PROJ-123', 'marker')            # the key, or ''
jira.assign_issue(key, jira.current_user())        # '' unassigns
jira.get_issues_status(['PROJ-123', key], 'status')
comment_id = jira.add_preformatted_comment(key, 'Progress:', 'kept\n  exactly\n')
jira.update_preformatted_comment(key, comment_id, 'Progress:', 'changed\n')
jira.list_comments(key)                            # newest first, unfiltered
```

Comment bodies and descriptions are sent as ADF (Jira Cloud REST v3).

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

Selected by the host when its issue platform is `jira`. Routed through
`task_core_lib`'s `TaskClientFactory`.

## Tests

```
jira_core_lib/jira_core_lib/tests/
```
