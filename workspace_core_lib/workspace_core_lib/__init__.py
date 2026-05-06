"""Public surface of ``workspace_core_lib``.

Hosts typically need only :class:`WorkspaceCoreLib`; the rest is
re-exported for power users who construct the layers manually
(e.g. tests that want to swap the data-access in isolation).
"""

from workspace_core_lib.workspace_core_lib.workspace_core_lib import (
    WorkspaceCoreLib,
)
from workspace_core_lib.workspace_core_lib.data_layers.data.workspace_record import (
    SUPPORTED_WORKSPACE_STATUSES,
    WORKSPACE_STATUS_ACTIVE,
    WORKSPACE_STATUS_DONE,
    WORKSPACE_STATUS_ERRORED,
    WORKSPACE_STATUS_PROVISIONING,
    WORKSPACE_STATUS_REVIEW,
    WORKSPACE_STATUS_TERMINATED,
    WorkspaceRecord,
)
from workspace_core_lib.workspace_core_lib.data_layers.data_access.workspace_data_access import (
    DEFAULT_METADATA_FILENAME,
    WorkspaceDataAccess,
)
from workspace_core_lib.workspace_core_lib.data_layers.service.orphan_workspace_scanner_service import (
    OrphanWorkspace,
    OrphanWorkspaceScannerService,
)
from workspace_core_lib.workspace_core_lib.data_layers.service.workspace_service import (
    DEFAULT_PREFLIGHT_LOG_FILENAME,
    WorkspaceService,
)


__all__ = [
    'WorkspaceCoreLib',
    'WorkspaceService',
    'WorkspaceDataAccess',
    'WorkspaceRecord',
    'OrphanWorkspaceScannerService',
    'OrphanWorkspace',
    'SUPPORTED_WORKSPACE_STATUSES',
    'WORKSPACE_STATUS_PROVISIONING',
    'WORKSPACE_STATUS_ACTIVE',
    'WORKSPACE_STATUS_REVIEW',
    'WORKSPACE_STATUS_DONE',
    'WORKSPACE_STATUS_ERRORED',
    'WORKSPACE_STATUS_TERMINATED',
    'DEFAULT_METADATA_FILENAME',
    'DEFAULT_PREFLIGHT_LOG_FILENAME',
]
