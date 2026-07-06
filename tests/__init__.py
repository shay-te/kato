from __future__ import annotations

import os
import tempfile
from pathlib import Path
import unittest

# ---------------------------------------------------------------------------
# Machine-state isolation: kato keeps operator sidecar stores under ~/.kato
# (forgotten tasks, approvals, plan-mode locks, settings, workspaces, …).
# Unit tests must NEVER read or write the operator's real files — a live
# kato on the same machine would otherwise leak state into assertions
# (e.g. a forgotten ``PROJ-1`` fixture silently filtering every test task)
# and tests would leak fixtures back into the operator's UI. Redirect every
# store to a per-run temp dir. ``setdefault`` so a test (or operator) that
# sets an explicit override still wins; tests that ``pop`` a key fall back
# to kato's default path semantics unchanged.
# ---------------------------------------------------------------------------
_ISOLATED_KATO_HOME = Path(tempfile.mkdtemp(prefix='kato-tests-home-'))
for _key, _name in (
    ('KATO_FORGOTTEN_TASKS_PATH', 'forgotten_tasks.json'),
    ('KATO_READ_ONLY_REPOS_PATH', 'read_only_repos.json'),
    ('KATO_PLAN_MODE_PATH', 'plan_mode.json'),
    ('KATO_APPROVED_REPOSITORIES_PATH', 'approved-repositories.json'),
    ('KATO_ACTION_GUARD_AUDIT_PATH', 'action-guard-audit.log'),
    ('KATO_AUDIT_LOG_PATH', 'audit.log.jsonl'),
    ('KATO_SETTINGS_FILE', 'settings.json'),
    ('KATO_WORKSPACES_ROOT', 'workspaces'),
    ('KATO_SESSION_STATE_DIR', 'sessions'),
):
    os.environ.setdefault(_key, str(_ISOLATED_KATO_HOME / _name))


def load_tests(loader: unittest.TestLoader, tests: unittest.TestSuite, pattern: str | None):
    suite = unittest.TestSuite()
    suite.addTests(tests)
    repo_root = Path(__file__).resolve().parents[1]
    for tests_dir in [
        repo_root / 'repository_core_lib' / 'repository_core_lib' / 'tests',
        repo_root / 'task_core_lib' / 'task_core_lib' / 'tests',
        repo_root / 'youtrack_core_lib' / 'youtrack_core_lib' / 'tests',
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
