import subprocess
import tempfile
import types
import unittest
from pathlib import Path

from kato_core_lib.data_layers.data.fields import RepositoryFields
from kato_core_lib.data_layers.service.repository_inventory_service import (
    RepositoryInventoryService,
)
from tests.utils import build_task


class RepositoryInventoryServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client_repo = types.SimpleNamespace(
            id='client',
            display_name='Client',
            local_path='/workspace/client',
            provider_base_url='https://bitbucket.example',
            repo_slug='client',
            aliases=['frontend'],
        )
        self.backend_repo = types.SimpleNamespace(
            id='backend',
            display_name='Backend',
            local_path='/workspace/backend',
            provider_base_url='https://github.example/api/v3',
            repo_slug='backend',
            aliases=['api'],
        )

    def test_provider_api_defaults_from_source_maps_credentials(self) -> None:
        source = types.SimpleNamespace(
            github_issues=types.SimpleNamespace(
                base_url='https://api.github.com',
                token='gh-token',
                username='gh-user',
                api_email='',
            ),
            gitlab_issues=types.SimpleNamespace(
                base_url='https://gitlab.example/api/v4',
                token='gl-token',
                username='gl-user',
                api_email='',
            ),
            bitbucket_issues=types.SimpleNamespace(
                base_url='https://api.bitbucket.org/2.0',
                token='bb-token',
                username='bb-user',
                api_email='bb-api@example.com',
            ),
        )

        defaults = RepositoryInventoryService._provider_api_defaults_from_source(source)

        self.assertEqual(defaults['github'][RepositoryFields.PROVIDER_BASE_URL], 'https://api.github.com')
        self.assertEqual(defaults['github']['token'], 'gh-token')
        self.assertEqual(defaults['github']['username'], 'gh-user')
        self.assertEqual(defaults['gitlab'][RepositoryFields.PROVIDER_BASE_URL], 'https://gitlab.example/api/v4')
        self.assertEqual(defaults['gitlab']['token'], 'gl-token')
        self.assertEqual(defaults['gitlab']['username'], 'gl-user')
        self.assertEqual(defaults['bitbucket'][RepositoryFields.PROVIDER_BASE_URL], 'https://api.bitbucket.org/2.0')
        self.assertEqual(defaults['bitbucket']['token'], 'bb-token')
        self.assertEqual(defaults['bitbucket']['username'], 'bb-user')
        self.assertEqual(defaults['bitbucket']['api_email'], 'bb-api@example.com')

    def test_validate_connections_rejects_duplicate_repository_ids(self) -> None:
        repositories = [
            types.SimpleNamespace(
                id='client',
                display_name='Client',
                local_path='.',
                repo_slug='client',
                aliases=['frontend'],
            ),
            types.SimpleNamespace(
                id='client',
                display_name='Client 2',
                local_path='.',
                repo_slug='client-2',
                aliases=['ui'],
            ),
        ]

        service = RepositoryInventoryService(repositories)

        with self.assertRaisesRegex(ValueError, 'duplicate repository id'):
            service.validate_connections()

    def test_validate_connections_rejects_duplicate_aliases(self) -> None:
        repositories = [
            types.SimpleNamespace(
                id='client',
                display_name='Client',
                local_path='.',
                repo_slug='client',
                aliases=['shared'],
            ),
            types.SimpleNamespace(
                id='backend',
                display_name='Backend',
                local_path='.',
                repo_slug='backend',
                aliases=['shared'],
            ),
        ]

        service = RepositoryInventoryService(repositories)

        with self.assertRaisesRegex(ValueError, 'duplicate repository alias'):
            service.validate_connections()

    def test_discovered_inventory_tolerates_duplicate_aliases(self) -> None:
        # Two AUTO-DISCOVERED clones of one remote (``foo`` and ``foo-new``)
        # share the origin slug alias. Unlike an explicit-config collision,
        # this is unavoidable and must NOT empty the whole inventory (the bug:
        # the add-repo picker showed nothing + task pickup crashed).
        repositories = [
            types.SimpleNamespace(
                id='foo', display_name='Foo', local_path='/x/foo',
                repo_slug='foo', aliases=[],
            ),
            types.SimpleNamespace(
                id='foo-new', display_name='Foo New', local_path='/x/foo-new',
                repo_slug='foo', aliases=[],
            ),
        ]
        service = RepositoryInventoryService(repositories)
        # Simulate the discovery source so the collision is tolerated.
        service._inventory_from_discovery = True

        result = service.repositories  # triggers validation; must NOT raise

        self.assertEqual({r.id for r in result}, {'foo', 'foo-new'})

    def test_resolve_task_repositories_matches_multiple_repositories_from_task_text(self) -> None:
        service = RepositoryInventoryService([self.client_repo, self.backend_repo])
        task = build_task(description='Update client and backend endpoints')

        repositories = service.resolve_task_repositories(task)

        self.assertEqual([repository.id for repository in repositories], ['client', 'backend'])

    def test_resolve_task_repositories_uses_repo_tags_before_task_text(self) -> None:
        service = RepositoryInventoryService([self.client_repo, self.backend_repo])
        task = build_task(
            summary='Update client and backend endpoints',
            description='This should not drive selection when tags are present.',
            tags=[f'{RepositoryFields.REPOSITORY_TAG_PREFIX}backend'],
        )

        repositories = service.resolve_task_repositories(task)

        self.assertEqual([repository.id for repository in repositories], ['backend'])

    def test_resolve_task_repositories_matches_multiple_repo_tags(self) -> None:
        service = RepositoryInventoryService([self.client_repo, self.backend_repo])
        task = build_task(
            tags=[
                f'{RepositoryFields.REPOSITORY_TAG_PREFIX}client',
                f'{RepositoryFields.REPOSITORY_TAG_PREFIX}backend',
            ]
        )

        repositories = service.resolve_task_repositories(task)

        self.assertEqual([repository.id for repository in repositories], ['client', 'backend'])

    def test_resolve_task_repositories_defaults_to_single_repo_when_nothing_matches(self) -> None:
        service = RepositoryInventoryService([self.client_repo])
        task = build_task(
            summary='Random task without repo hints',
            description='No mention of any repository name here.',
            tags=['some-other-label'],
        )

        repositories = service.resolve_task_repositories(task)

        self.assertEqual([repository.id for repository in repositories], ['client'])

    def test_resolve_task_repositories_rejects_unmatched_repo_tags(self) -> None:
        service = RepositoryInventoryService([self.client_repo, self.backend_repo])

        with self.assertRaisesRegex(
            ValueError,
            'no configured repository matched repo tags on task PROJ-1',
        ):
            service.resolve_task_repositories(
                build_task(tags=[f'{RepositoryFields.REPOSITORY_TAG_PREFIX}missing'])
            )

    def test_resolve_task_repositories_rejects_partial_substrings(self) -> None:
        # Two repos: forces the "must actually match" path. With a single repo
        # we deliberately default; that's covered separately.
        repository_a = types.SimpleNamespace(
            id='myrepo',
            display_name='My Repository',
            local_path='/workspace/myrepo',
            repo_slug='myrepo',
            aliases=['myrepo'],
        )
        repository_b = types.SimpleNamespace(
            id='other',
            display_name='Other Repository',
            local_path='/workspace/other',
            repo_slug='other',
            aliases=['other'],
        )
        service = RepositoryInventoryService([repository_a, repository_b])

        with self.assertRaisesRegex(ValueError, 'no configured repository matched task PROJ-1'):
            service.resolve_task_repositories(build_task(description='myrepo-extra needs changes'))

    def test_get_repository_returns_known_repository_and_rejects_unknown(self) -> None:
        service = RepositoryInventoryService([self.client_repo, self.backend_repo])

        self.assertIs(service.get_repository('backend'), self.backend_repo)
        with self.assertRaisesRegex(ValueError, 'unknown repository id: missing'):
            service.get_repository('missing')

    def test_discovers_repositories_from_root_and_ignores_configured_folders(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            projects_root = Path(temp_dir)
            generic_repo = projects_root / 'project'
            ignored_repo = projects_root / 'ignored-repo'
            self._create_git_repository(
                generic_repo,
                'git@bitbucket.org:acme/ob-love-admin-client.git',
            )
            self._create_git_repository(
                ignored_repo,
                'git@bitbucket.org:acme/ignored.git',
            )

            service = RepositoryInventoryService(
                types.SimpleNamespace(
                    repositories=[],
                    repository_root_path=str(projects_root),
                    ignored_repository_folders='ignored-repo',
                ),
            )

            # Lazy discovery: must access repositories while the temp
            # directory still exists, since the walk now happens at
            # first-read time, not at __init__.
            repositories = service.repositories
            self.assertEqual([repository.id for repository in repositories], ['ob-love-admin-client'])
            self.assertEqual(repositories[0].display_name, 'Ob Love Admin Client')
            self.assertEqual(repositories[0].repo_slug, 'ob-love-admin-client')
            self.assertEqual(repositories[0].aliases, ['project', 'ob-love-admin-client'])

    @staticmethod
    def _create_git_repository(path: Path, remote_url: str) -> None:
        path.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ['git', 'init', '-q'],
            cwd=path,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ['git', 'remote', 'add', 'origin', remote_url],
            cwd=path,
            check=True,
            capture_output=True,
            text=True,
        )


class EnsureRepositoriesConcurrencyTests(unittest.TestCase):
    """Regression: ``_ensure_repositories``'s check-then-populate sequence
    used to have no lock. The background warm-up thread
    (``AgentService.warm_up_repository_inventory``) and a concurrent
    webserver request thread (GET /api/repositories, served
    threaded=True) could both observe ``self._repositories is None``
    before either finished its disk walk; whichever finished LAST
    overwrote the other's already-filtered/validated result with its
    own raw list — and since ``_inventory_validated`` was already True
    by then, the denylist filter and duplicate-alias validation never
    ran on that overwrite. This silently and PERMANENTLY (until
    restart) bypassed KATO_REPOSITORY_DENYLIST.

    Verified before this lock existed: 5 concurrent callers produced 5
    raw (unfiltered) loads and the denylisted repo id survived in at
    least one thread's returned list.
    """

    def test_concurrent_callers_load_and_filter_exactly_once(self) -> None:
        import threading
        import time
        from unittest.mock import patch

        service = RepositoryInventoryService(
            repositories_config=types.SimpleNamespace(repositories=[]),
        )
        call_counts = {'load': 0, 'filter': 0}

        def slow_load(_config):
            call_counts['load'] += 1
            time.sleep(0.05)
            return [
                types.SimpleNamespace(id='repoA'),
                types.SimpleNamespace(id='denied-repo'),
            ]

        def fake_filter(repositories):
            call_counts['filter'] += 1
            return [r for r in repositories if r.id != 'denied-repo']

        with patch.object(
            service, '_load_repositories', side_effect=slow_load,
        ), patch.object(
            service, '_filter_denied_repositories', side_effect=fake_filter,
        ), patch.object(service, '_validate_inventory', return_value=None):
            results: list[list[object]] = []
            results_lock = threading.Lock()

            def worker() -> None:
                result = service._ensure_repositories()
                with results_lock:
                    results.append(result)

            threads = [threading.Thread(target=worker) for _ in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        self.assertEqual(call_counts['load'], 1, 'disk walk ran more than once')
        self.assertEqual(call_counts['filter'], 1, 'denylist filter ran more than once')
        for result in results:
            ids = [r.id for r in result]
            self.assertNotIn('denied-repo', ids, 'denylist was bypassed on one caller')
            self.assertEqual(ids, ['repoA'])
