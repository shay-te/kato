# youtrack-core-lib

YouTrack ticket-platform implementation. Plugs into
[`task_core_lib`](../task_core_lib/) as the `Platform.YOUTRACK`
provider.

## Public API

```python
from youtrack_core_lib.youtrack_core_lib.youtrack_core_lib import (
    YouTrackCoreLib,
)

yt = YouTrackCoreLib(cfg).issue
yt.get_assigned_tasks(assignee='kato', states=['Open'])
yt.add_comment(task_id='UNA-123', body='…')
yt.move_task_to_state(task_id='UNA-123', state='In Review')
```

## Config

Driven by Hydra under `core_lib.youtrack_core_lib`:

```yaml
core_lib:
  youtrack_core_lib:
    base_url: https://your-org.youtrack.cloud
    token: ${oc.env:YOUTRACK_API_TOKEN}
    max_retries: 3
```

Default config schema lives in
[`youtrack_core_lib/config/`](youtrack_core_lib/config/) and is
shipped with the package.

## Where this fits

Selected by kato when `KATO_ISSUE_PLATFORM=youtrack`. Routed
through `task_core_lib`'s `TaskClientFactory`.

## Tests

```
youtrack_core_lib/youtrack_core_lib/tests/
```
