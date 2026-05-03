"""Tag-vs-ignore-list interaction tests for RepositoryInventoryService.

Pins down the rule: if a task carries a ``kato:repo:<name>`` tag whose
``<name>`` appears in ``KATO_IGNORED_REPOSITORY_FOLDERS``, kato refuses
to resolve the task — loudly, with a message that names the offending
tag and points the operator at the contradiction. Previous behavior
silently dropped the unmatched tag, which let kato proceed with a
partial repository set or a misleading "no repos matched" error. The
ignore list is the operator's explicit "do not touch this folder"
declaration; a tag pointing at it is a configuration mistake worth
failing on.
"""

from __future__ import annotations

import subprocess
import tempfile
import types
import unittest
from pathlib import Path

from kato_core_lib.data_layers.data.fields import RepositoryFields
from kato_core_lib.data_layers.service.repository_inventory_service import (
    RepositoryInventoryService,
)
from utils import build_task


REPO_TAG_PREFIX = RepositoryFields.REPOSITORY_TAG_PREFIX


def _create_git_repo(path: Path, remote_url: str) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ['git', 'init', '-q'], cwd=path, check=True, capture_output=True,
    )
    subprocess.run(
        ['git', 'remote', 'add', 'origin', remote_url],
        cwd=path, check=True, capture_output=True,
    )


class IgnoreListTagRejectionTests(unittest.TestCase):
    """Behavior when the ignore list and a task tag point at the same name."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _build_service(
        self,
        *,
        ignored_folders: object,
        explicit_repositories: list | None = None,
    ) -> RepositoryInventoryService:
        return RepositoryInventoryService(
            types.SimpleNamespace(
                repositories=explicit_repositories or [],
                repository_root_path=str(self.root),
                ignored_repository_folders=ignored_folders,
            ),
        )

    # ----- single tag: ignored folder -----

    def test_single_tag_pointing_at_ignored_folder_raises(self) -> None:
        _create_git_repo(
            self.root / 'forbidden-repo',
            'git@bitbucket.org:acme/forbidden.git',
        )
        service = self._build_service(ignored_folders='forbidden-repo')

        with self.assertRaisesRegex(
            ValueError,
            r'KATO_IGNORED_REPOSITORY_FOLDERS: forbidden-repo',
        ):
            service.resolve_task_repositories(
                build_task(tags=[f'{REPO_TAG_PREFIX}forbidden-repo']),
            )

    def test_single_tag_in_ignore_list_rejects_even_when_folder_does_not_exist(
        self,
    ) -> None:
        # The ignore list is a contract about what kato will not touch.
        # Whether the folder happens to exist on disk shouldn't change
        # the answer — the tag still points at a forbidden name.
        service = self._build_service(ignored_folders='forbidden-repo')

        with self.assertRaisesRegex(
            ValueError, r'KATO_IGNORED_REPOSITORY_FOLDERS: forbidden-repo',
        ):
            service.resolve_task_repositories(
                build_task(tags=[f'{REPO_TAG_PREFIX}forbidden-repo']),
            )

    def test_error_message_explains_how_to_fix(self) -> None:
        _create_git_repo(
            self.root / 'forbidden-repo',
            'git@bitbucket.org:acme/forbidden.git',
        )
        service = self._build_service(ignored_folders='forbidden-repo')

        with self.assertRaises(ValueError) as ctx:
            service.resolve_task_repositories(
                build_task(tags=[f'{REPO_TAG_PREFIX}forbidden-repo']),
            )

        message = str(ctx.exception)
        self.assertIn('Either remove the kato:repo:<name> tag', message)
        self.assertIn('KATO_IGNORED_REPOSITORY_FOLDERS', message)

    # ----- mixed tags: some valid, some ignored -----

    def test_mixed_tags_with_one_ignored_rejects_the_whole_task(self) -> None:
        # Even when other tags are perfectly resolvable, the presence of
        # an ignored-list tag is a configuration error and stops the
        # whole task — silent partial resolution is the bug we're
        # explicitly preventing.
        _create_git_repo(
            self.root / 'legit-repo',
            'git@bitbucket.org:acme/legit.git',
        )
        _create_git_repo(
            self.root / 'forbidden-repo',
            'git@bitbucket.org:acme/forbidden.git',
        )
        service = self._build_service(ignored_folders='forbidden-repo')

        with self.assertRaisesRegex(
            ValueError, r'forbidden-repo',
        ):
            service.resolve_task_repositories(
                build_task(
                    tags=[
                        f'{REPO_TAG_PREFIX}legit-repo',
                        f'{REPO_TAG_PREFIX}forbidden-repo',
                    ],
                ),
            )

    def test_multiple_ignored_tags_are_all_listed_in_the_error(self) -> None:
        service = self._build_service(
            ignored_folders='forbidden-a,forbidden-b',
        )

        with self.assertRaises(ValueError) as ctx:
            service.resolve_task_repositories(
                build_task(
                    tags=[
                        f'{REPO_TAG_PREFIX}forbidden-a',
                        f'{REPO_TAG_PREFIX}forbidden-b',
                    ],
                ),
            )

        message = str(ctx.exception)
        self.assertIn('forbidden-a', message)
        self.assertIn('forbidden-b', message)

    # ----- case sensitivity -----

    def test_ignore_list_match_is_case_insensitive(self) -> None:
        _create_git_repo(
            self.root / 'Forbidden-Repo',
            'git@bitbucket.org:acme/forbidden.git',
        )
        service = self._build_service(ignored_folders='Forbidden-Repo')

        with self.assertRaisesRegex(
            ValueError, r'KATO_IGNORED_REPOSITORY_FOLDERS',
        ):
            service.resolve_task_repositories(
                build_task(tags=[f'{REPO_TAG_PREFIX}forbidden-repo']),
            )

    def test_tag_case_does_not_matter_for_ignore_match(self) -> None:
        service = self._build_service(ignored_folders='forbidden-repo')

        with self.assertRaisesRegex(
            ValueError, r'KATO_IGNORED_REPOSITORY_FOLDERS',
        ):
            service.resolve_task_repositories(
                build_task(tags=[f'{REPO_TAG_PREFIX}FORBIDDEN-REPO']),
            )

    # ----- ignore-list parsing forms -----

    def test_ignored_folders_list_form_is_honored(self) -> None:
        service = self._build_service(
            ignored_folders=['forbidden-repo', 'other-ignored'],
        )

        with self.assertRaisesRegex(
            ValueError, r'KATO_IGNORED_REPOSITORY_FOLDERS',
        ):
            service.resolve_task_repositories(
                build_task(tags=[f'{REPO_TAG_PREFIX}forbidden-repo']),
            )

    def test_ignored_folders_comma_string_with_spaces_is_honored(self) -> None:
        service = self._build_service(
            ignored_folders='  legit , forbidden-repo , another  ',
        )

        with self.assertRaisesRegex(
            ValueError, r'KATO_IGNORED_REPOSITORY_FOLDERS',
        ):
            service.resolve_task_repositories(
                build_task(tags=[f'{REPO_TAG_PREFIX}forbidden-repo']),
            )

    # ----- non-rejection cases (control group) -----

    def test_tag_pointing_at_legit_folder_succeeds(self) -> None:
        _create_git_repo(
            self.root / 'legit-repo',
            'git@bitbucket.org:acme/legit.git',
        )
        service = self._build_service(ignored_folders='something-else')

        repositories = service.resolve_task_repositories(
            build_task(tags=[f'{REPO_TAG_PREFIX}legit-repo']),
        )

        self.assertEqual([repo.id for repo in repositories], ['legit-repo'])

    def test_tag_with_empty_ignore_list_is_not_rejected(self) -> None:
        _create_git_repo(
            self.root / 'legit-repo',
            'git@bitbucket.org:acme/legit.git',
        )
        service = self._build_service(ignored_folders='')

        repositories = service.resolve_task_repositories(
            build_task(tags=[f'{REPO_TAG_PREFIX}legit-repo']),
        )

        self.assertEqual([repo.id for repo in repositories], ['legit-repo'])

    def test_rejection_is_idempotent_across_repeated_resolutions(self) -> None:
        # Pin down: a tag rejected on call N must be rejected the same
        # way on call N+1, regardless of any in-memory caching the
        # resolver introduces. Future caching changes can't accidentally
        # cache "unknown" and degrade rejection into a silent skip.
        _create_git_repo(
            self.root / 'forbidden-repo',
            'git@bitbucket.org:acme/forbidden.git',
        )
        service = self._build_service(ignored_folders='forbidden-repo')
        task = build_task(tags=[f'{REPO_TAG_PREFIX}forbidden-repo'])

        first_messages: list[str] = []
        with self.assertRaises(ValueError) as ctx:
            service.resolve_task_repositories(task)
        first_messages.append(str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            service.resolve_task_repositories(task)
        first_messages.append(str(ctx.exception))

        self.assertEqual(first_messages[0], first_messages[1])
        self.assertIn('KATO_IGNORED_REPOSITORY_FOLDERS', first_messages[0])

    def test_direct_fast_path_resolver_returns_none_for_ignored_folder(self) -> None:
        # Defense in depth: even called outside the rejection-aware
        # caller, ``_discover_repository_at_named_folder`` must refuse
        # to materialize a repository entry for a folder in the ignore
        # list. Otherwise a future caller could bypass the rejection
        # and silently use the forbidden repo.
        _create_git_repo(
            self.root / 'forbidden-repo',
            'git@bitbucket.org:acme/forbidden.git',
        )
        service = self._build_service(ignored_folders='forbidden-repo')

        result = service._discover_repository_at_named_folder('forbidden-repo')

        self.assertIsNone(result)

    def test_resolved_tag_is_cached_and_skips_repeat_filesystem_scan(self) -> None:
        # First resolution costs a stat + a one-shot inventory entry
        # build. Second call must come from the cache without going to
        # disk again — defense against future caching changes that
        # could re-run the walk on every task scan.
        from unittest.mock import patch as _patch

        _create_git_repo(
            self.root / 'legit-repo',
            'git@bitbucket.org:acme/legit.git',
        )
        service = self._build_service(ignored_folders='something-else')
        task = build_task(tags=[f'{REPO_TAG_PREFIX}legit-repo'])

        first = service.resolve_task_repositories(task)
        with _patch.object(
            service, '_discover_repository_at_named_folder',
        ) as mock_discover, _patch.object(
            service, '_ensure_repositories',
        ) as mock_ensure:
            second = service.resolve_task_repositories(task)
            mock_discover.assert_not_called()
            mock_ensure.assert_not_called()

        self.assertEqual(
            [r.id for r in first], [r.id for r in second],
        )

    def test_direct_fast_path_resolver_returns_entry_for_legit_folder(self) -> None:
        # Counterpart of the test above: when the folder is NOT ignored,
        # the fast-path resolver does build a usable entry. Together
        # the two tests pin the contract: ignore-list match → None,
        # otherwise → real entry.
        _create_git_repo(
            self.root / 'legit-repo',
            'git@bitbucket.org:acme/legit.git',
        )
        service = self._build_service(ignored_folders='something-else')

        result = service._discover_repository_at_named_folder('legit-repo')

        self.assertIsNotNone(result)
        self.assertEqual(result.id, 'legit-repo')

    def test_ignore_list_rejection_takes_precedence_over_no_match_error(self) -> None:
        # If there were no ignore list, an unresolvable tag would yield
        # the generic "no configured repository matched repo tags"
        # error. The ignore-list rejection happens first and gives a
        # more actionable diagnostic — nail down that ordering.
        service = self._build_service(ignored_folders='forbidden-repo')

        with self.assertRaises(ValueError) as ctx:
            service.resolve_task_repositories(
                build_task(tags=[f'{REPO_TAG_PREFIX}forbidden-repo']),
            )

        self.assertIn('KATO_IGNORED_REPOSITORY_FOLDERS', str(ctx.exception))
        self.assertNotIn('no configured repository matched', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
