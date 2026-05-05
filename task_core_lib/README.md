# task-core-lib

Ticket-platform abstraction. Hides the choice of YouTrack /
Jira / GitHub Issues / GitLab Issues / Bitbucket Issues behind a
single `TaskCoreLib(platform, cfg, max_retries).issue` client.

## Public API

```python
from task_core_lib.task_core_lib.task_core_lib import TaskCoreLib
from task_core_lib.task_core_lib.platform import Platform

ticket = TaskCoreLib(Platform.YOUTRACK, cfg, max_retries=3).issue
ticket.get_assigned_tasks(assignee='kato', states=['Open', 'In Progress'])
ticket.add_comment(task_id='UNA-123', body='…')
ticket.move_task_to_state(task_id='UNA-123', state='In Review')
```

The `Platform` enum is the routing key; new providers register
through `TaskClientFactory`.

## Where this fits

Kato calls into this lib from
`kato_core_lib/data_layers/service/task_service.py`. Each provider
implementation lives in its own sibling package
(`youtrack_core_lib`, `jira_core_lib`, `github_core_lib`,
`gitlab_core_lib`, `bitbucket_core_lib`); this lib only owns the
contract + the factory.

## Tests

```
task_core_lib/task_core_lib/tests/
```
