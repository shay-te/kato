from __future__ import annotations

from pathlib import Path
import unittest


def load_tests(loader: unittest.TestLoader, tests: unittest.TestSuite, pattern: str | None):
    suite = unittest.TestSuite()
    suite.addTests(tests)
    repo_root = Path(__file__).resolve().parents[1]
    for tests_dir in [
        repo_root / 'github_core_lib' / 'github_core_lib' / 'tests',
        repo_root / 'bitbucket_core_lib' / 'bitbucket_core_lib' / 'tests',
        repo_root / 'gitlab_core_lib' / 'gitlab_core_lib' / 'tests',
        repo_root / 'jira_core_lib' / 'jira_core_lib' / 'tests',
        repo_root / 'vcs_provider_contracts' / 'vcs_provider_contracts' / 'tests',
    ]:
        if tests_dir.is_dir():
            suite.addTests(
                loader.discover(
                    start_dir=str(tests_dir),
                    pattern=pattern or 'test*.py',
                    top_level_dir=str(repo_root),
                )
            )
    return suite
