# git-core-lib

Provider-agnostic git operations. A small, dependency-light library that wraps
the local `git` executable in a safe, well-tested mixin you can mix into any
service class.

It does one thing: run `git` subprocesses correctly. It knows nothing about
GitHub/GitLab/Bitbucket APIs, issue trackers, or any product workflow — every
provider-specific concern (HTTP auth headers, what to do with a branch) is
either a subclass hook or the caller's job.

## Public API

`GitClientMixin` is the engine. Mix it into a host class that supplies a
`self.logger`; optionally override one hook to inject HTTP credentials.

```python
import logging
from git_core_lib.git_core_lib.client.git_client import GitClientMixin


class RepositoryService(GitClientMixin):
    def __init__(self):
        self.logger = logging.getLogger("repo")

    # Optional hook — return an Authorization header for HTTPS remotes.
    # Default is '' (no auth, e.g. SSH remotes).
    def _build_git_http_auth_header(self, repository) -> str:
        return "Basic ..."  # provider-specific credentials


service = RepositoryService()

default_branch = RepositoryService._infer_default_branch("/work/example-repo")
service._sync_branch_with_remote("/work/example-repo", "feature/widget")
status = service._working_tree_status("/work/example-repo")
matches = service.git_grep("/work/example-repo", "build_widget")
service._push_branch("/work/example-repo", "feature/widget")
```

What the mixin gives you:

- **Reference / status queries** — `_infer_default_branch`, `_current_branch`,
  `_working_tree_status`, `_git_reference_exists`, `_ahead_count`,
  `_left_right_commit_counts`.
- **Content search** — `git_grep(local_path, query)` returns
  `[{path, line, text}]` over tracked + untracked files (fixed-string,
  case-insensitive, binary files skipped); "no matches" is not an error.
- **Push / sync / rebase** — `_push_branch` (with automatic fetch-and-rebase
  retry on a non-fast-forward rejection), `_sync_branch_with_remote`,
  `_pull_destination_branch`, and conflict-safe rebase that aborts and
  re-raises on failure.
- **Safe-by-default subprocess core** — every invocation disables git hooks
  (`core.hooksPath=/dev/null`), suppresses the terminal credential prompt
  (`GIT_TERMINAL_PROMPT=0`), applies `safe.directory`, captures text output,
  and recovers from a stale `index.lock` left by a crashed process.

### Helpers

Standalone utilities under `git_core_lib/git_core_lib/helpers/`:

- `repository_discovery_utils` — walk a tree for `.git` folders, parse remote
  URLs into `(provider, owner, repo_slug)`, build review/compare URLs.
- `git_clean_utils` — parse `git status --porcelain` output and detect
  generated artifacts (build dirs, `__pycache__`, `.pyc`) safe to reset/clean.

## No peer dependencies

Standard library plus the `git` executable invoked via `subprocess` — nothing
else. This library imports no other `*_core_lib` peer and contains no
product-specific names or text. Anything specific to a host application is
passed in as a constructor argument, a config value, or the
`_build_git_http_auth_header` hook.

## Tests

```
python -m unittest discover -s git_core_lib/git_core_lib/tests -p "test_*.py"
```

Tests live inside the library at `git_core_lib/git_core_lib/tests/`. They use
`unittest` + `unittest.mock` only — `subprocess.run` is patched, so no real
`git` and no network access are required. See `test_flow.py` for an
end-to-end, A-Z walkthrough of the primary workflow.
