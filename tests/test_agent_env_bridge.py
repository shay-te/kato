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

from kato_core_lib.kato_core_lib import _export_agent_env_from_kato_config


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


if __name__ == '__main__':
    unittest.main()
