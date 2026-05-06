"""Top-level entry point for ``workspace_core_lib``.

Standard core-lib shape: a thin façade that wires the data-access
layer, the service layer, and the orphan scanner together. Hosts
construct one ``WorkspaceCoreLib`` and then access ``workspaces``
(the public service) and ``orphan_scanner`` for boot-time recovery.

Why a class instead of free helpers: keeps the host-side wiring
to one line (``lib = WorkspaceCoreLib(...); lib.workspaces.create(...)``)
and matches the upstream ``core-lib`` pattern so a developer used
to that shape can navigate this package on day one.
"""

from __future__ import annotations

import logging
import os

from core_lib.core_lib import CoreLib

from workspace_core_lib.workspace_core_lib.data_layers.data_access.workspace_data_access import (
    DEFAULT_METADATA_FILENAME,
    WorkspaceDataAccess,
)
from workspace_core_lib.workspace_core_lib.data_layers.service.orphan_workspace_scanner_service import (
    OrphanWorkspaceScannerService,
)
from workspace_core_lib.workspace_core_lib.data_layers.service.workspace_service import (
    DEFAULT_PREFLIGHT_LOG_FILENAME,
    WorkspaceService,
)


class WorkspaceCoreLib(CoreLib):
    """Wires the workspace data-access + service together.

    Construction parameters:

    * ``root`` — folder to put workspaces under. Created on first
      use.
    * ``max_parallel_tasks`` — informational concurrency cap
      surfaced via ``workspaces.max_parallel_tasks``. Defaults to
      ``1``; clamped to a minimum of ``1``.
    * ``metadata_filename`` — name of the per-workspace metadata
      file. Defaults to ``.workspace-meta.json``. Override only
      when you have legacy data on disk under a different name.
    * ``preflight_log_filename`` — name of the per-workspace
      provisioning step log. Defaults to ``.workspace-preflight.log``.
    * ``logger`` — optional logger; defaults to a module logger.
    """

    def __init__(
        self,
        *,
        root: str | os.PathLike[str],
        max_parallel_tasks: int = 1,
        metadata_filename: str = DEFAULT_METADATA_FILENAME,
        preflight_log_filename: str = DEFAULT_PREFLIGHT_LOG_FILENAME,
        logger: logging.Logger | None = None,
    ) -> None:
        CoreLib.__init__(self)
        self._logger = logger or logging.getLogger(self.__class__.__name__)
        self._data_access = WorkspaceDataAccess(
            root=root,
            metadata_filename=metadata_filename,
            logger=self._logger,
        )
        self.workspaces = WorkspaceService(
            self._data_access,
            max_parallel_tasks=max_parallel_tasks,
            preflight_log_filename=preflight_log_filename,
            logger=self._logger,
        )
        self.orphan_scanner = OrphanWorkspaceScannerService(self._data_access)
