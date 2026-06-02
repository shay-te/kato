#!/usr/bin/env python3
"""Run the kato unit-test suite across every owning package.

``./kato test`` shells into this script. It discovers tests from
each registered location and reports the combined outcome with a
single non-zero exit on any failure.

Why a script and not ``unittest discover`` directly: each core-lib
sits in its own top-level directory (``kato_core_lib/``,
``sandbox_core_lib/``, …), and ``unittest discover`` rejects start
paths that are not inside the working directory it was invoked
from. Calling ``discover`` once per root from inside this script
respects that constraint without forcing every consumer to know
where each package keeps its tests.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

# Test roots are AUTO-DISCOVERED rather than hand-listed. A curated list
# silently went stale and dropped whole core-libs (codex/git/task/provider/…)
# from every ``kato test`` run — the worst kind of test-suite bug because it
# looks green. Discovery finds every in-repo directory named ``tests`` that
# holds at least one ``test_*.py``, so a newly-extracted core-lib is picked up
# automatically and the suite can never go partial again. Virtualenvs, VCS,
# caches and build output are pruned so we only ever run THIS repo's tests.
_SKIP_DIRS = frozenset({
    '.venv', 'venv', 'env', '.git', 'node_modules', '__pycache__',
    '.tox', '.pytest_cache', '.mypy_cache', 'build', 'dist', '.eggs',
})


def discover_test_roots(repo_root: Path) -> list[str]:
    """Every in-repo ``tests`` directory that owns ``test_*.py`` files."""
    roots: list[str] = []
    for dirpath, dirnames, filenames in os.walk(repo_root):
        # Prune unwanted subtrees in place so os.walk never descends them.
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        path = Path(dirpath)
        if path.name != 'tests':
            continue
        if any(f.startswith('test_') and f.endswith('.py') for f in filenames):
            roots.append(str(path.relative_to(repo_root)))
    return sorted(roots)


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    # Tests import from the kato + sibling packages by absolute
    # name (``kato_core_lib.X``, ``sandbox_core_lib.Y``…). When this
    # script is launched directly (``python scripts/run_all_tests.py``)
    # ``sys.path`` carries ``scripts/`` rather than the repo root,
    # which would break those imports. Prepend the repo root so the
    # in-repo packages resolve the same way ``python -m unittest
    # discover`` does from CWD=repo_root.
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    runner = unittest.TextTestRunner(verbosity=1)
    failures = 0
    test_roots = discover_test_roots(repo_root)
    print(f'Discovered {len(test_roots)} test roots:', flush=True)
    for relative in test_roots:
        print(f'  - {relative}', flush=True)
    for relative in test_roots:
        start = repo_root / relative
        if not start.is_dir():
            continue
        loader = unittest.TestLoader()
        # Don't pin ``top_level_dir`` — unittest's auto-detection
        # gives the same module names ``unittest discover -s tests``
        # used to produce, so existing tests resolve their helper
        # imports unchanged. Pinning it to the repo root surprisingly
        # changed how modules were loaded and dropped large parts of
        # the suite from the run.
        suite = loader.discover(
            start_dir=str(start),
            pattern='test_*.py',
        )
        print(f'\n=== {relative} ===', flush=True)
        result = runner.run(suite)
        if not result.wasSuccessful():
            failures += 1
    return 0 if failures == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
