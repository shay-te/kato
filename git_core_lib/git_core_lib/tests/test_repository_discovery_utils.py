"""Coverage for ``git_core_lib/helpers/repository_discovery_utils.py``."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from git_core_lib.git_core_lib.helpers.repository_discovery_utils import (
    DiscoveredRepository,
    build_discovered_repository,
    discover_git_repositories,
    display_name_from_repo_slug,
    git_config_path,
    parse_git_remote_url,
    read_git_remote_url,
    remote_web_base_url,
    repository_id_from_name,
    review_url_for_remote,
)


def _make_git_dir(path: Path, remote_url: str = '') -> None:
    """Create a fake ``.git/`` directory with a config holding a remote URL."""
    git_dir = path / '.git'
    git_dir.mkdir(parents=True)
    config_text = '[core]\n\trepositoryformatversion = 0\n'
    if remote_url:
        config_text += f'[remote "origin"]\n\turl = {remote_url}\n'
    (git_dir / 'config').write_text(config_text, encoding='utf-8')


class DiscoverGitRepositoriesTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_returns_empty_when_root_does_not_exist(self) -> None:
        # Line 37: missing root → []
        self.assertEqual(discover_git_repositories(str(self.root / 'nope')), [])

    def test_returns_empty_when_root_is_not_a_dir(self) -> None:
        # Line 37: root exists but isn't a directory.
        file_root = self.root / 'file.txt'
        file_root.write_text('not a dir')
        self.assertEqual(discover_git_repositories(str(file_root)), [])

    def test_discovers_repository_with_remote(self) -> None:
        repo = self.root / 'myrepo'
        repo.mkdir()
        _make_git_dir(repo, 'git@github.com:org/myrepo.git')
        results = discover_git_repositories(str(self.root))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].provider, 'github')
        self.assertEqual(results[0].repo_slug, 'myrepo')

    def test_skips_skip_dirs(self) -> None:
        # .venv and node_modules etc. should not be walked into.
        skipped = self.root / 'node_modules' / 'pkg'
        skipped.mkdir(parents=True)
        _make_git_dir(skipped)
        # Real repo at top level should still be found.
        real = self.root / 'real'
        real.mkdir()
        _make_git_dir(real)
        results = discover_git_repositories(str(self.root))
        names = [Path(r.local_path).name for r in results]
        self.assertIn('real', names)
        self.assertNotIn('pkg', names)

    def test_ignored_folders_argument_filters_results(self) -> None:
        repo_keep = self.root / 'keep'
        repo_keep.mkdir()
        _make_git_dir(repo_keep)
        repo_drop = self.root / 'drop'
        repo_drop.mkdir()
        _make_git_dir(repo_drop)
        results = discover_git_repositories(str(self.root), ignored_folders=['drop'])
        names = [Path(r.local_path).name for r in results]
        self.assertIn('keep', names)
        self.assertNotIn('drop', names)


class BuildDiscoveredRepositoryTests(unittest.TestCase):
    def test_builds_from_path_with_remote(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / 'r'
            repo.mkdir()
            _make_git_dir(repo, 'git@github.com:org/r.git')
            result = build_discovered_repository(repo)
        self.assertEqual(result.provider, 'github')
        self.assertEqual(result.owner, 'org')
        self.assertEqual(result.repo_slug, 'r')


class ReadGitRemoteUrlTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name) / 'r'
        self.repo.mkdir()

    def test_returns_empty_when_no_config(self) -> None:
        # Line 78: no .git/config → ''.
        self.assertEqual(read_git_remote_url(self.repo), '')

    def test_returns_empty_when_config_parse_error(self) -> None:
        # Lines 83-84: configparser raises → return ''.
        (self.repo / '.git').mkdir()
        (self.repo / '.git' / 'config').write_text('[[[broken syntax\n')
        self.assertEqual(read_git_remote_url(self.repo), '')

    def test_reads_origin_url(self) -> None:
        _make_git_dir(self.repo, 'https://github.com/org/r.git')
        self.assertEqual(
            read_git_remote_url(self.repo),
            'https://github.com/org/r.git',
        )

    def test_falls_back_to_first_remote_section(self) -> None:
        # Lines 88-91: no "origin" remote but other remote sections exist
        # → return the first one with a url.
        (self.repo / '.git').mkdir()
        (self.repo / '.git' / 'config').write_text(
            '[core]\n\trepositoryformatversion = 0\n'
            '[remote "upstream"]\n\turl = https://github.com/upstream/r.git\n',
        )
        self.assertEqual(
            read_git_remote_url(self.repo),
            'https://github.com/upstream/r.git',
        )

    def test_returns_empty_when_no_remote_at_all(self) -> None:
        # No remote section at all → ''.
        (self.repo / '.git').mkdir()
        (self.repo / '.git' / 'config').write_text('[core]\n\trepositoryformatversion = 0\n')
        self.assertEqual(read_git_remote_url(self.repo), '')


class GitConfigPathTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name) / 'r'
        self.repo.mkdir()

    def test_returns_dir_config_path(self) -> None:
        (self.repo / '.git').mkdir()
        result = git_config_path(self.repo)
        self.assertEqual(result, self.repo / '.git' / 'config')

    def test_returns_none_when_no_git_entry(self) -> None:
        # Line 99: no ``.git`` file or dir → None.
        self.assertIsNone(git_config_path(self.repo))

    def test_returns_none_when_git_file_empty(self) -> None:
        # Lines 102-103: ``.git`` file exists but is empty → None.
        (self.repo / '.git').write_text('')
        self.assertIsNone(git_config_path(self.repo))

    def test_returns_none_when_git_file_does_not_start_with_gitdir(self) -> None:
        # Lines 106-107: first line isn't ``gitdir:`` → None.
        (self.repo / '.git').write_text('not a gitdir reference\n')
        self.assertIsNone(git_config_path(self.repo))

    def test_handles_worktree_git_file_with_relative_path(self) -> None:
        # Lines 108-112: gitdir reference with relative path.
        real_git = Path(self._tmp.name) / 'shared.git'
        real_git.mkdir()
        (self.repo / '.git').write_text('gitdir: ../shared.git\n')
        result = git_config_path(self.repo)
        self.assertEqual(result.name, 'config')
        self.assertIn('shared.git', str(result))

    def test_handles_worktree_git_file_with_absolute_path(self) -> None:
        real_git = Path(self._tmp.name) / 'shared.git'
        real_git.mkdir()
        (self.repo / '.git').write_text(f'gitdir: {real_git}\n')
        result = git_config_path(self.repo)
        self.assertEqual(result, real_git / 'config')


class ParseGitRemoteUrlTests(unittest.TestCase):
    def test_returns_empty_for_blank_url(self) -> None:
        # Line 117: ``if not remote_url`` → return ('', '', '').
        self.assertEqual(parse_git_remote_url(''), ('', '', ''))

    def test_parses_https_url(self) -> None:
        provider, owner, slug = parse_git_remote_url('https://github.com/org/myrepo.git')
        self.assertEqual((provider, owner, slug), ('github', 'org', 'myrepo'))

    def test_parses_ssh_url(self) -> None:
        provider, owner, slug = parse_git_remote_url('git@gitlab.com:group/project.git')
        self.assertEqual((provider, owner, slug), ('gitlab', 'group', 'project'))

    def test_returns_empty_when_url_has_no_path(self) -> None:
        # Lines 131-132: host parsed but no path → return ('', '', '').
        self.assertEqual(parse_git_remote_url('https://github.com'), ('', '', ''))

    def test_returns_empty_when_ssh_format_unparsable(self) -> None:
        # Match doesn't fire → host/path stay '' → return ('', '', '').
        self.assertEqual(parse_git_remote_url('garbage no scheme'), ('', '', ''))

    def test_returns_empty_when_path_has_only_one_segment(self) -> None:
        # Line 139: path doesn't have at least two segments → return ('', '', '').
        self.assertEqual(parse_git_remote_url('https://github.com/just-one'), ('', '', ''))

    def test_handles_bitbucket_provider(self) -> None:
        result = parse_git_remote_url('https://bitbucket.org/org/repo.git')
        self.assertEqual(result, ('bitbucket', 'org', 'repo'))

    def test_unknown_host_returns_empty_provider(self) -> None:
        # Provider is '' but owner/slug populated.
        result = parse_git_remote_url('https://example.com/org/repo.git')
        self.assertEqual(result, ('', 'org', 'repo'))

    def test_multi_segment_owner(self) -> None:
        # GitLab groups can be nested → owner is everything except the last segment.
        result = parse_git_remote_url('https://gitlab.com/group/subgroup/repo.git')
        self.assertEqual(result, ('gitlab', 'group/subgroup', 'repo'))


class RepositoryIdFromNameTests(unittest.TestCase):
    def test_lowercases_and_dashifies(self) -> None:
        self.assertEqual(repository_id_from_name('My Repo!'), 'my-repo')

    def test_returns_primary_for_empty_or_all_dashes(self) -> None:
        # Line 153: ``strip('-') or 'primary'`` — fallback.
        self.assertEqual(repository_id_from_name(''), 'primary')
        self.assertEqual(repository_id_from_name('!!!'), 'primary')


class DisplayNameFromRepoSlugTests(unittest.TestCase):
    def test_capitalizes_each_word(self) -> None:
        self.assertEqual(
            display_name_from_repo_slug('my-cool-repo'),
            'My Cool Repo',
        )

    def test_returns_default_for_blank(self) -> None:
        # Line 159: no words after split → default.
        self.assertEqual(display_name_from_repo_slug(''), 'Primary Repository')
        self.assertEqual(display_name_from_repo_slug('---'), 'Primary Repository')


class RemoteWebBaseUrlTests(unittest.TestCase):
    def test_returns_empty_for_blank(self) -> None:
        self.assertEqual(remote_web_base_url(''), '')

    def test_parses_https_url(self) -> None:
        self.assertEqual(
            remote_web_base_url('https://github.com/org/repo.git'),
            'https://github.com',
        )

    def test_includes_port_when_present(self) -> None:
        self.assertEqual(
            remote_web_base_url('https://internal:8443/org/repo.git'),
            'https://internal:8443',
        )

    def test_returns_empty_when_https_has_no_hostname(self) -> None:
        # Line 169: parsed.hostname is empty → ''.
        self.assertEqual(remote_web_base_url('https://'), '')

    def test_parses_ssh_url(self) -> None:
        self.assertEqual(
            remote_web_base_url('git@github.com:org/repo.git'),
            'https://github.com',
        )

    def test_returns_empty_when_ssh_unparsable(self) -> None:
        # Line 176: re.match returns None → ''.
        self.assertEqual(remote_web_base_url('garbage'), '')


class ReviewUrlForRemoteTests(unittest.TestCase):
    def test_returns_empty_when_no_web_base(self) -> None:
        # Line 190: blank web base → ''.
        result = review_url_for_remote('', 'github', 'org', 'r', 'feat', 'main')
        self.assertEqual(result, '')

    def test_returns_empty_when_no_owner(self) -> None:
        result = review_url_for_remote(
            'https://github.com/x/y.git', 'github', '', 'r', 'feat', 'main',
        )
        self.assertEqual(result, '')

    def test_github_compare_url(self) -> None:
        result = review_url_for_remote(
            'https://github.com/org/r.git', 'github', 'org', 'r', 'feat/x', 'main',
        )
        # Source branch is URL-encoded (``/`` → ``%2F``).
        self.assertIn('/org/r/compare/main...feat%2Fx', result)
        self.assertIn('expand=1', result)

    def test_gitlab_merge_request_url(self) -> None:
        result = review_url_for_remote(
            'https://gitlab.com/group/r.git', 'gitlab', 'group', 'r',
            'feat/x', 'main',
        )
        self.assertIn('/merge_requests/new', result)
        # Slash in branch name is URL-encoded.
        self.assertIn('feat%2Fx', result)

    def test_bitbucket_pull_request_url(self) -> None:
        result = review_url_for_remote(
            'https://bitbucket.org/org/r.git', 'bitbucket', 'org', 'r',
            'feat/x', 'main',
        )
        self.assertIn('/pull-requests/new', result)

    def test_unknown_provider_returns_repository_base(self) -> None:
        # Line 209: ``return f'{web_base_url}/{repository_path}'`` fallback.
        result = review_url_for_remote(
            'https://example.com/org/r.git', 'unknown', 'org', 'r', 'feat', 'main',
        )
        self.assertEqual(result, 'https://example.com/org/r')


if __name__ == '__main__':
    unittest.main()
