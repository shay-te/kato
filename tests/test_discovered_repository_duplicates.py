"""One folder name twice must not cost every repository its source folder.

Operator report (Windows): "Update source" on a task with one repository said
``skipped transactions: duplicate repository id: neo-dealer-webapp``, a repository
the task never touched, and the log said ``unable to determine pull request
provider``. The walk found ``neo-dealer-webapp`` twice, the check that refuses a
duplicate id refused the WHOLE list, and it refused again on every read: no
source folder and no pull requests for any repository.

Two ways there: kato's own task clones, when the workspaces folder sits inside
``REPOSITORY_ROOT_PATH``, and two of the operator's folders with one name.

Real folders, real ``git init``, the real RepositoryService.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from kato_core_lib.data_layers.service.repository_service import RepositoryService


def make_repository(path: Path, remote: str = '') -> None:
    path.mkdir(parents=True)
    subprocess.run(['git', 'init', '-q'], cwd=str(path), check=True)
    if remote:
        subprocess.run(['git', 'remote', 'add', 'origin', remote], cwd=str(path), check=True)


class DiscoveredDuplicatesTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name).resolve()
        self.root = self.home / 'dev'
        make_repository(self.root / 'transactions', 'https://github.com/acme/transactions.git')
        make_repository(self.root / 'neo-dealer-webapp', 'https://github.com/acme/neo-dealer-webapp.git')
        self.env = patch.dict(os.environ, {'KATO_WORKSPACES_ROOT': str(self.home / 'away')})
        self.env.start()
        self.addCleanup(self.env.stop)
        settings = patch(
            'kato_core_lib.helpers.kato_settings_store_utils.read_kato_settings', return_value={},
        )
        settings.start()
        self.addCleanup(settings.stop)

    def service(self, **config) -> RepositoryService:
        return RepositoryService(
            SimpleNamespace(repository_root_path=str(self.root), **config), 3,
        )

    def ids(self, service: RepositoryService) -> list[str]:
        return sorted(repository.id for repository in service.repositories)

    def test_kato_s_own_clones_are_not_repositories(self) -> None:
        workspaces = self.root / '.kato' / 'workspaces'
        make_repository(workspaces / 'LOCAL-2' / 'neo-dealer-webapp')
        make_repository(workspaces / 'LOCAL-3' / 'neo-dealer-webapp')
        make_repository(workspaces / 'LOCAL-1' / 'transactions')
        # A clone with no source in the root is the case only skipping the
        # folder catches: nothing else would keep it out of the list.
        make_repository(workspaces / 'LOCAL-4' / 'only-a-clone')
        with patch.dict(os.environ, {'KATO_WORKSPACES_ROOT': str(workspaces)}):
            service = self.service()

            self.assertEqual(self.ids(service), ['neo-dealer-webapp', 'transactions'])
            self.assertEqual(
                service.get_repository('transactions').local_path,
                str(self.root / 'transactions'),
            )

    def test_the_default_workspaces_folder_counts_too(self) -> None:
        # "Empty = ~/.kato/workspaces": most installs never set the variable.
        self.root = self.home
        make_repository(self.home / 'neo-dealer-webapp')
        make_repository(self.home / '.kato' / 'workspaces' / 'LOCAL-2' / 'neo-dealer-webapp')
        with patch.dict(os.environ, {'KATO_WORKSPACES_ROOT': ''}), \
                patch('pathlib.Path.home', return_value=self.home):
            self.assertEqual(self.service().get_repository('neo-dealer-webapp').local_path,
                             str(self.home / 'neo-dealer-webapp'))

    def test_a_second_folder_with_one_name_leaves_every_repository_working(self) -> None:
        make_repository(self.root / 'backup' / 'neo-dealer-webapp')
        service = self.service()

        with self.assertLogs(service.logger, 'WARNING') as logs:
            self.assertEqual(self.ids(service), ['neo-dealer-webapp', 'transactions'])

        # What his one-repository task needed: the source folder and the provider.
        transactions = service.get_repository('transactions')
        self.assertEqual(transactions.local_path, str(self.root / 'transactions'))
        self.assertEqual(transactions.provider, 'github')
        # And the log names both folders, so the operator can tell which is which.
        self.assertIn(str(self.root / 'backup' / 'neo-dealer-webapp'), logs.output[0])
        self.assertIn(str(self.root / 'neo-dealer-webapp'), logs.output[0])

    def test_the_folder_closest_to_the_root_wins_wherever_it_sorts(self) -> None:
        # ``backup/`` sorts before ``neo-...`` and ``zzz/`` after: depth decides,
        # because that is the folder ``kato:repo:<id>`` finds at ``<root>/<id>``.
        make_repository(self.root / 'backup' / 'neo-dealer-webapp')
        make_repository(self.root / 'zzz' / 'neo-dealer-webapp')
        service = self.service()

        self.assertEqual(
            service.get_repository('neo-dealer-webapp').local_path,
            str(self.root / 'neo-dealer-webapp'),
        )

    def test_folders_equally_deep_settle_the_same_way_every_time(self) -> None:
        make_repository(self.root / 'a' / 'api')
        make_repository(self.root / 'b' / 'api')

        for _ in range(3):
            self.assertEqual(
                self.service().get_repository('api').local_path, str(self.root / 'a' / 'api'),
            )

    def test_the_order_of_the_list_is_unchanged(self) -> None:
        make_repository(self.root / 'backup' / 'neo-dealer-webapp')
        make_repository(self.root / 'backup' / 'aaa')

        self.assertEqual(
            [repository.id for repository in self.service().repositories],
            ['aaa', 'neo-dealer-webapp', 'transactions'],
        )

    def test_a_duplicate_in_written_config_is_still_refused(self) -> None:
        # A list someone typed by hand: that is a mistake to stop for.
        entry = SimpleNamespace(id='api', display_name='api', local_path='.', repo_slug='api', aliases=[])
        with self.assertRaisesRegex(ValueError, 'duplicate repository id'):
            RepositoryService([entry, entry], 3).validate_connections()


if __name__ == '__main__':
    unittest.main()
