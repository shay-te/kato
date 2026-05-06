"""Kato-side config glue for ``workspace_core_lib``.

The actual workspace machinery (folder creation, metadata I/O,
status transitions, preflight log, orphan scanning) lives in
``workspace_core_lib``. This module is the thin kato adapter:

* Reads kato's Hydra config + ``KATO_*`` env vars to pick the
  workspace root and parallelism cap.
* Pins the metadata + preflight-log filenames to kato's historical
  values (``.kato-meta.json`` / ``.kato-preflight.log``) so existing
  on-disk workspaces remain readable without a rename pass.
* Re-exports the lib's public types under the legacy import paths
  so kato modules that imported from ``workspace_manager`` keep
  working unchanged.

The provisioning helper (``provision_task_workspace_clones``) lives
in :mod:`workspace_provisioning_service` — kato-specific glue that
wires the kato Task model to the lib's services.
"""

from __future__ import annotations

import os
from pathlib import Path

from workspace_core_lib.workspace_core_lib import (  # noqa: F401 — public re-exports
    SUPPORTED_WORKSPACE_STATUSES,
    WORKSPACE_STATUS_ACTIVE,
    WORKSPACE_STATUS_DONE,
    WORKSPACE_STATUS_ERRORED,
    WORKSPACE_STATUS_PROVISIONING,
    WORKSPACE_STATUS_REVIEW,
    WORKSPACE_STATUS_TERMINATED,
    WorkspaceCoreLib,
    WorkspaceRecord,
    WorkspaceService,
)

from kato_core_lib.helpers.text_utils import normalized_text


_KATO_METADATA_FILENAME = '.kato-meta.json'
_KATO_PREFLIGHT_LOG_FILENAME = '.kato-preflight.log'
_DEFAULT_ROOT_DIR_NAME = '.kato/workspaces'


# Kato calls this ``WorkspaceManager`` for historical reasons. The
# object is a ``WorkspaceService`` from ``workspace_core_lib`` —
# the alias is kept so the orchestrator's existing field name
# (``self.workspace_manager``) and import paths read naturally.
WorkspaceManager = WorkspaceService


def build_workspace_manager_from_config(
    open_cfg,
    agent_backend: str,  # noqa: ARG002 — accepted for API parity
) -> WorkspaceService:
    """Build kato's workspace service from Hydra config + env vars.

    Backend-agnostic: both Claude and OpenHands flows use
    workspaces for parallel isolation, so we don't gate on
    ``agent_backend``. Pins the metadata + preflight-log filenames
    to kato's historical names so existing ``.kato-meta.json``
    files on disk continue to load.
    """
    configured_root = normalized_text(
        getattr(open_cfg, 'workspaces_root', '')
        or os.environ.get('KATO_WORKSPACES_ROOT', '')
    )
    root = configured_root or str(Path.home() / _DEFAULT_ROOT_DIR_NAME)
    max_parallel = _coerce_positive_int(
        getattr(open_cfg, 'max_parallel_tasks', None)
        or os.environ.get('KATO_MAX_PARALLEL_TASKS', ''),
        default=1,
    )
    lib = WorkspaceCoreLib(
        root=root,
        max_parallel_tasks=max_parallel,
        metadata_filename=_KATO_METADATA_FILENAME,
        preflight_log_filename=_KATO_PREFLIGHT_LOG_FILENAME,
    )
    return lib.workspaces


# Legacy-shaped class accessor. ``WorkspaceManager.from_config(...)``
# was the entry point the orchestrator used pre-extraction; keep the
# signature alive by attaching the builder as a classmethod-style
# attribute on the alias.
WorkspaceManager.from_config = staticmethod(build_workspace_manager_from_config)


def _coerce_positive_int(value, *, default: int) -> int:
    if value in (None, ''):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default
