# workspace-core-lib

Per-task on-disk workspaces for parallel agent execution.

A *workspace* is a folder named after a task id (e.g. `PROJ-123/`)
that contains a fresh clone of every repository the task touches,
plus a metadata sidecar describing the workspace's lifecycle. Two
tasks running in parallel get two independent workspace folders,
so they never share branch state or trip over each other's
git index.

This library owns the workspace folders and their metadata. It
does NOT clone git repositories, run agent subprocesses, talk to
ticket trackers, or know what an "agent" is — those are host
concerns. The library just gives the host a clean place to keep
"what state is each task's workspace in, and where on disk does
it live."

## Why this is a sibling library

Workspaces are a generic concept. They appear in any system that:

* Runs multiple tasks in parallel and needs to isolate their
  on-disk state from each other,
* Wants persistent metadata for those tasks that survives a host
  restart (so a UI can rehydrate the list of in-flight work),
* Needs to recover from out-of-band folder drops (an operator
  cloned a repo manually and the host should adopt it).

Originally this code lived inside kato. Lifting it out lets a
non-kato consumer drop the package in and get the same
guarantees without inheriting kato's task model, agent
backends, or webserver.

## Layout

Standard core-lib shape:

```
workspace_core_lib/workspace_core_lib/
├── workspace_core_lib.py                  ← entry point class (extends CoreLib)
├── data_layers/
│   ├── data/
│   │   └── workspace_record.py            ← WorkspaceRecord dataclass + status constants
│   ├── data_access/
│   │   └── workspace_data_access.py       ← JSON-file CRUD on records
│   └── service/
│       ├── workspace_service.py           ← public façade (lifecycle + paths + preflight log)
│       └── orphan_workspace_scanner_service.py
└── helpers/
    └── atomic_write_utils.py              ← atomic JSON write
```

Layering rule (Onion Architecture, the upstream `core-lib`
convention):

```
WorkspaceCoreLib (entry point)
    └── WorkspaceService          ← what hosts call
        └── WorkspaceDataAccess   ← filesystem persistence
            └── WorkspaceRecord   ← pure data
```

Higher layers may call lower; lower never reaches up.

## Public API

```python
from workspace_core_lib.workspace_core_lib import WorkspaceCoreLib

lib = WorkspaceCoreLib(root='~/.myapp/workspaces', max_parallel_tasks=4)

# Create a workspace for one task.
record = lib.workspaces.create(
    task_id='PROJ-1',
    task_summary='profile page rewrite',
    repository_ids=['client', 'backend'],
)

# Path the host should clone each repo into.
client_path = lib.workspaces.repository_path('PROJ-1', 'client')

# Lifecycle transitions.
lib.workspaces.update_status('PROJ-1', 'active')

# Bind an agent session to the workspace (kato writes the Claude
# session uuid here, but the lib doesn't care which agent).
lib.workspaces.update_agent_session(
    'PROJ-1', agent_session_id='sess-uuid', cwd=str(client_path),
)

# Provisioning progress trail consumed by UIs.
lib.workspaces.append_preflight_log('PROJ-1', 'cloning 1/2: client')
entries = lib.workspaces.read_preflight_log('PROJ-1')

# Find folders the operator dropped under the root that the lib
# didn't create itself.
orphans = lib.orphan_scanner.scan()
```

### Lifecycle states

```
provisioning → active → review → done
                    ↘ errored
                    ↘ terminated
```

`provisioning` is the initial state set by `create()`. The host
moves the workspace through the rest as the task progresses.
Constants live in `workspace_core_lib.workspace_core_lib`:

```python
from workspace_core_lib.workspace_core_lib import (
    WORKSPACE_STATUS_PROVISIONING,
    WORKSPACE_STATUS_ACTIVE,
    WORKSPACE_STATUS_REVIEW,
    WORKSPACE_STATUS_DONE,
    WORKSPACE_STATUS_ERRORED,
    WORKSPACE_STATUS_TERMINATED,
)
```

`update_status()` rejects any value not in this set.

### Configuration

Everything is constructor-injected. The library never reads
environment variables or YAML files itself — that belongs to the
host. The knobs:

| Parameter                  | Default                       | Purpose |
|----------------------------|-------------------------------|---------|
| `root`                     | (required)                    | Folder to put workspaces under. Created on first use. |
| `max_parallel_tasks`       | `1`                           | Informational concurrency cap surfaced via `lib.workspaces.max_parallel_tasks`. Clamped to ≥1. |
| `metadata_filename`        | `.workspace-meta.json`        | Per-workspace metadata file name. Override only when you have legacy data on disk under a different name. |
| `preflight_log_filename`   | `.workspace-preflight.log`    | Per-workspace provisioning step log file name. |

### On-disk layout

For task `PROJ-1` with two repos `client` and `backend`:

```
<root>/
└── PROJ-1/
    ├── .workspace-meta.json          ← record (atomic JSON)
    ├── .workspace-preflight.log      ← append-only step log
    ├── client/                       ← host-provided git clone
    └── backend/                      ← host-provided git clone
```

The library writes only the two dotfiles. Repositories are
cloned by the host (this lib doesn't shell out to git).

### Atomic writes

Metadata writes go through `helpers.atomic_write_utils.atomic_write_json`:
write to a tmpfile in the same directory, fsync, then `os.replace`
over the destination. Concurrent readers see either the old or
the new file — never a torn one.

### Thread safety

`WorkspaceService` is safe under concurrent callers (one internal
re-entrant lock). The orchestrator's main thread can `create()`
while a webserver thread `list_workspaces()` and a worker
`update_status()` — none of them tear each other.

### Backwards-compatible field rename

Older deployments (pre-extraction kato) persisted the agent
session id under `claude_session_id`. This library's canonical
name is `agent_session_id` (generic — no agent leakage in the
public API). To avoid forcing a disk migration:

* `WorkspaceRecord.from_dict()` accepts **either** key on read.
* `WorkspaceRecord.to_dict()` always writes the new name.

A workspace gets rewritten in the canonical form on its first
update after upgrade. Hosts don't need to do anything.

## Orphan recovery

`OrphanWorkspaceScannerService` finds folders under the root
that lack metadata. The host decides what to do with them
(adopt, skip, discard) — that decision is host policy and
belongs in host code:

```python
for orphan in lib.orphan_scanner.scan():
    if not orphan.git_repository_dirs:
        continue  # not a clone-bearing folder; skip
    # Host policy: is orphan.task_id a real ticket? If yes,
    # adopt by calling lib.workspaces.create(...) with the
    # same id and metadata the host knows.
```

The scanner reports each orphan's path, its (would-be) task id,
and the names of immediate subdirectories that contain a `.git`
entry — enough for the host to decide without re-walking the
filesystem itself.

## What's NOT in this library

| Concern                                  | Lives where           |
|------------------------------------------|-----------------------|
| Cloning git repositories                 | Host (e.g. kato's `RepositoryService`) |
| Talking to ticket trackers               | Host                  |
| Running agent subprocesses               | Host                  |
| Cleanup policy ("when to delete a workspace") | Host                  |
| Mapping orphan folders to live tickets   | Host                  |
| Agent session resume mechanics           | Host (e.g. kato's `ClaudeSessionManager`) |

This library knows where workspaces live and tracks their state.
Everything else is host policy.

## Tests

```
workspace_core_lib/workspace_core_lib/tests/
├── test_workspace_record.py
├── test_workspace_data_access.py
├── test_workspace_service.py
├── test_orphan_workspace_scanner_service.py
└── test_workspace_core_lib.py
```

Pin the data-layer round-trip (including legacy field rename
compat), the data-access CRUD + atomic writes + corrupt-file
tolerance, the service-level lifecycle and partial-update
semantics, the scanner's filesystem walk, and the entry-point
wiring.

Run via the kato suite runner:

```
python scripts/run_all_tests.py
```

Or directly:

```
python -m unittest discover -s workspace_core_lib/workspace_core_lib/tests
```

## Dependencies

* `core-lib >= 0.2.0` — the upstream `CoreLib`, `Service`, and
  `DataAccess` base classes. Keeps this library aligned with
  every other core-lib in the ecosystem so a developer fluent
  in any one of them lands in this package on day one.

No kato imports. No environment variable reads. No file-format
conventions outside the constructor.
