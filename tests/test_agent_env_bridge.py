"""kato exports its ``KATO_*`` agent-facing config under the generic
``AGENT_*`` env names the agnostic agent libs read.

All ``KATO_*`` variables live only in kato_core_lib; the shared
``agent_core_lib`` reads product-agnostic ``AGENT_*`` names. This bridge keeps
operators setting the documented ``KATO_*`` vars while the libs stay clean.
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from kato_core_lib.kato_core_lib import (
    _export_agent_env_from_kato_config,
    _export_agent_workspaces_root,
)


class AgentEnvBridgeTests(unittest.TestCase):
    def _clean_env(self):
        ctx = patch.dict(os.environ, {}, clear=False)
        ctx.start()
        os.environ.pop('KATO_IGNORED_REPOSITORY_FOLDERS', None)
        os.environ.pop('AGENT_IGNORED_REPOSITORY_FOLDERS', None)
        self.addCleanup(ctx.stop)

    def test_exports_ignored_repository_folders_to_generic_name(self) -> None:
        self._clean_env()
        os.environ['KATO_IGNORED_REPOSITORY_FOLDERS'] = 'secret-client, legacy-api'
        _export_agent_env_from_kato_config()
        self.assertEqual(
            os.environ.get('AGENT_IGNORED_REPOSITORY_FOLDERS'),
            'secret-client, legacy-api',
        )

    def test_no_op_when_kato_var_unset(self) -> None:
        self._clean_env()
        _export_agent_env_from_kato_config()
        self.assertNotIn('AGENT_IGNORED_REPOSITORY_FOLDERS', os.environ)

    def test_explicit_generic_value_is_not_overwritten(self) -> None:
        self._clean_env()
        os.environ['KATO_IGNORED_REPOSITORY_FOLDERS'] = 'from-kato'
        os.environ['AGENT_IGNORED_REPOSITORY_FOLDERS'] = 'explicit-wins'
        _export_agent_env_from_kato_config()
        self.assertEqual(
            os.environ.get('AGENT_IGNORED_REPOSITORY_FOLDERS'), 'explicit-wins',
        )



class WorkspacesRootExportTests(unittest.TestCase):
    """The agent libs resolve each task folder against AGENT_WORKSPACES_ROOT.

    Without it, the CLI's memory directory cannot be pinned inside the task
    folder, and the agent goes back to ~/.claude/projects/... — the thing the
    operator was tired of correcting by hand on every task.
    """

    def _clean(self):
        ctx = patch.dict(os.environ, {}, clear=False)
        ctx.start()
        os.environ.pop('AGENT_WORKSPACES_ROOT', None)
        self.addCleanup(ctx.stop)

    def test_the_built_managers_root_is_exported(self) -> None:
        from pathlib import Path
        from types import SimpleNamespace
        self._clean()
        _export_agent_workspaces_root(SimpleNamespace(root=Path('/srv/workspaces')))
        self.assertEqual(os.environ.get('AGENT_WORKSPACES_ROOT'), '/srv/workspaces')

    def test_the_default_root_is_exported_too(self) -> None:
        # The ~/.kato/workspaces default is applied inside the manager and was
        # never in the environment — re-deriving from KATO_WORKSPACES_ROOT
        # would leave a default install with no root at all.
        from types import SimpleNamespace
        self._clean()
        os.environ.pop('KATO_WORKSPACES_ROOT', None)
        default_root = os.path.join(os.path.expanduser('~'), '.kato', 'workspaces')
        _export_agent_workspaces_root(SimpleNamespace(root=default_root))
        self.assertEqual(os.environ.get('AGENT_WORKSPACES_ROOT'), default_root)

    def test_no_manager_exports_nothing(self) -> None:
        self._clean()
        _export_agent_workspaces_root(None)
        self.assertNotIn('AGENT_WORKSPACES_ROOT', os.environ)

    def test_an_explicit_generic_value_still_wins(self) -> None:
        from types import SimpleNamespace
        self._clean()
        os.environ['AGENT_WORKSPACES_ROOT'] = '/explicit'
        _export_agent_workspaces_root(SimpleNamespace(root='/from-manager'))
        self.assertEqual(os.environ.get('AGENT_WORKSPACES_ROOT'), '/explicit')



class CoreManagersExportTheRootTests(unittest.TestCase):
    """The export must happen WHERE the workspace manager is built.

    Mutation testing showed the helper tests above cannot see the call site:
    deleting the call from ``_build_core_managers`` left every one of them
    green, while in the running app no session would get a memory directory
    and the agent would go straight back to ~/.claude/projects/... . This drives
    the real method with its heavy collaborators stubbed.
    """

    def test_building_the_managers_exports_the_built_managers_root(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        import kato_core_lib.kato_core_lib as module

        ctx = patch.dict(os.environ, {}, clear=False)
        ctx.start()
        self.addCleanup(ctx.stop)
        os.environ.pop('AGENT_WORKSPACES_ROOT', None)

        manager = SimpleNamespace(root='/srv/built-root', max_parallel_tasks=1)
        instance = object.__new__(module.KatoCoreLib)
        instance.logger = MagicMock()
        instance.hook_runner = None
        stubs = [
            patch.object(module, 'resolved_agent_backend', return_value='claude'),
            patch.object(module, 'is_docker_mode_enabled', return_value=False),
            patch.object(module.WorkspaceManager, 'from_config', return_value=manager),
            patch.object(module, 'ParallelTaskRunner', MagicMock()),
            patch.object(module.PlanningSessionRunner, 'from_config', MagicMock()),
            patch.object(module.KatoCoreLib, '_build_session_manager', return_value=None),
            patch.object(module.KatoCoreLib, '_build_lessons_service', return_value=None),
        ]
        for stub in stubs:
            stub.start()
            self.addCleanup(stub.stop)

        instance._build_core_managers(SimpleNamespace())

        self.assertEqual(os.environ.get('AGENT_WORKSPACES_ROOT'), '/srv/built-root')


if __name__ == '__main__':
    unittest.main()
