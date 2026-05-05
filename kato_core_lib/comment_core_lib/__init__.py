"""Diff-tab review-comment store + sync.

Operators leave kato comments on a per-task workspace's diff in two
shapes: **local** (kato-only, scoped to the workspace) and **remote**
(pulled from / pushed to the source git platform's PR review-comment
API). Both surface in the Changes-tab UI through the same widget,
distinguished by a source badge.

The store is JSON at ``<workspace>/.kato-comments.json`` so an
operator can move workspaces between machines and the comment log
travels with them. Atomic writes via the existing
``atomic_write_json`` helper.

Public surface is intentionally small — the agent_service wires
``CommentService`` into the webserver, which exposes the CRUD
endpoints the UI talks to. Direct callers of the store should be
rare; route through the service.
"""

from kato_core_lib.comment_core_lib.comment_record import (
    CommentRecord,
    CommentSource,
    CommentStatus,
    KatoCommentStatus,
)
from kato_core_lib.comment_core_lib.comment_store import LocalCommentStore
