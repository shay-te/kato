# comment-core-lib (kato-internal)

Local JSON-backed store for code-review comments. Lives **inside**
`kato_core_lib/` (not as a sibling package) on purpose — it has
no consumer outside kato, and the abstractions here are tightly
coupled to kato's per-task workspace layout.

## What it does

Persists per-file, per-line review comments to
`<workspace>/.kato-comments.json`. Supports threading (root
comment + replies), resolution status, and a kato-pipeline status
field (`queued` / `in_progress` / `addressed` / `failed`).
Source-platform sync (Bitbucket / GitHub PR review comments
pulled into the local store, with kato's "addressed" replies
posted back) is wired by the surrounding service layer.

## Public API

```python
from kato_core_lib.comment_core_lib.comment_record import CommentRecord
from kato_core_lib.comment_core_lib.comment_store import CommentStore

store = CommentStore(workspace_path)
store.add(CommentRecord(...))
store.list(repo_id='client', file_path='src/foo.js')
store.resolve(comment_id, by='operator')
store.mark_kato_addressed(comment_id, sha='abc123')
```

The store is intentionally minimal — append + read + status
update. The "what to do with these comments" logic
(de-dupe with remote, decide which to fix, post replies) lives
in `kato_core_lib/data_layers/service/agent_service.py`.

## Why kato-internal

This module was extracted from a sprawling `agent_service.py`
to give the comment-store a clean, testable boundary. The right
shape became visible only after the split. There is no second
consumer today, and the contract leans on the per-task workspace
path convention — promoting it to a sibling package would force
a fake "make it generic" pass that helps no one.
