from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent_core_lib.agent_core_lib.helpers.project_conventions_utils import (
    CONVENTION_FILE_NAMES,
    repository_conventions_text,
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')


def repo_ns(root: Path, repo_id: str = 'PROJ-1') -> SimpleNamespace:
    return SimpleNamespace(local_path=str(root), id=repo_id)


class RepositoryConventionsTextTests(unittest.TestCase):
    def test_empty_or_none_list_returns_blank(self) -> None:
        self.assertEqual(repository_conventions_text([]), '')
        self.assertEqual(repository_conventions_text(None), '')

    def test_no_file_anywhere_returns_blank(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'repo'
            root.mkdir()
            self.assertEqual(repository_conventions_text([repo_ns(root)]), '')

    def test_a_blank_local_path_or_missing_directory_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = str(Path(tmp) / 'does-not-exist')
            repos = [
                SimpleNamespace(local_path='', id='blank'),
                SimpleNamespace(local_path=missing, id='missing'),
            ]
            self.assertEqual(repository_conventions_text(repos), '')

    def test_claude_md_at_the_root_is_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'repo'
            _write(root / 'CLAUDE.md', 'Commit messages must be Conventional Commits.')
            text = repository_conventions_text([repo_ns(root)])
            self.assertIn('Commit messages must be Conventional Commits.', text)
            self.assertIn('CLAUDE.md:', text)

    def test_agents_md_is_read_when_there_is_no_claude_md(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'repo'
            _write(root / 'AGENTS.md', 'PR descriptions need a Test plan section.')
            text = repository_conventions_text([repo_ns(root)])
            self.assertIn('PR descriptions need a Test plan section.', text)
            self.assertIn('AGENTS.md:', text)

    def test_claude_md_wins_over_agents_md_in_the_same_directory(self) -> None:
        # The real-world case this exists for: a developer's own CLAUDE.md,
        # the one Claude Code itself already reads by hand.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'repo'
            _write(root / 'CLAUDE.md', 'from CLAUDE.md')
            _write(root / 'AGENTS.md', 'from AGENTS.md')
            text = repository_conventions_text([repo_ns(root)])
            self.assertIn('from CLAUDE.md', text)
            self.assertNotIn('from AGENTS.md', text)

    def test_a_nested_directory_may_prefer_the_other_file_than_its_parent(self) -> None:
        # Each directory's own preference is independent — CLAUDE.md wins
        # where both exist, AGENTS.md is read where it is the only one.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'repo'
            _write(root / 'CLAUDE.md', 'root rule')
            _write(root / 'sub' / 'AGENTS.md', 'nested rule')
            text = repository_conventions_text([repo_ns(root)])
            self.assertIn('root rule', text)
            self.assertIn('nested rule', text)

    def test_multiple_repositories_are_concatenated_and_labeled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            client = Path(tmp) / 'client'
            backend = Path(tmp) / 'backend'
            _write(client / 'CLAUDE.md', 'client rule')
            _write(backend / 'AGENTS.md', 'backend rule')
            text = repository_conventions_text([
                repo_ns(client, 'client'), repo_ns(backend, 'backend'),
            ])
            self.assertIn('Repository client:', text)
            self.assertIn('client rule', text)
            self.assertIn('Repository backend:', text)
            self.assertIn('backend rule', text)

    def test_a_repository_with_nothing_is_skipped_but_others_still_render(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bare = Path(tmp) / 'bare'
            bare.mkdir()
            client = Path(tmp) / 'client'
            _write(client / 'CLAUDE.md', 'client rule')
            text = repository_conventions_text([
                repo_ns(bare, 'bare'), repo_ns(client, 'client'),
            ])
            self.assertNotIn('Repository bare:', text)
            self.assertIn('Repository client:', text)

    def test_git_directory_is_never_walked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'repo'
            _write(root / '.git' / 'CLAUDE.md', 'should never be read')
            self.assertEqual(repository_conventions_text([repo_ns(root)]), '')

    def test_file_names_checked_in_order(self) -> None:
        self.assertEqual(CONVENTION_FILE_NAMES, ('CLAUDE.md', 'AGENTS.md'))


if __name__ == '__main__':
    unittest.main()
