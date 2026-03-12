import unittest
from pathlib import Path
from unittest.mock import patch

import bootstrap  # noqa: F401

from openhands_agent.create_db import build_alembic_config, main
from utils import build_test_cfg


class CreateDbTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = build_test_cfg()

    def test_build_alembic_config_uses_project_migration_dir(self) -> None:
        alembic_cfg = build_alembic_config(self.cfg)

        self.assertEqual(
            alembic_cfg.get_main_option('sqlalchemy.url'),
            'sqlite:///:memory:',
        )
        self.assertEqual(
            alembic_cfg.get_main_option('version_table'),
            self.cfg.core_lib.alembic.version_table,
        )
        self.assertEqual(
            alembic_cfg.get_main_option('render_as_batch'),
            'true',
        )
        self.assertEqual(
            Path(alembic_cfg.get_main_option('script_location')),
            Path(__file__).resolve().parents[1]
            / 'openhands_agent'
            / self.cfg.core_lib.alembic.script_location,
        )

    def test_main_runs_alembic_upgrade_to_head(self) -> None:
        with patch('openhands_agent.create_db.command.upgrade') as mock_upgrade:
            result = main(self.cfg)

        self.assertEqual(result, 0)
        mock_upgrade.assert_called_once()
        alembic_cfg, revision = mock_upgrade.call_args.args
        self.assertEqual(revision, 'head')
        self.assertEqual(
            alembic_cfg.get_main_option('version_table'),
            self.cfg.core_lib.alembic.version_table,
        )
