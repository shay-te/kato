#!/usr/bin/env python3
"""Run the kato unit-test suite across every owning package.

``./kato test`` shells into this script. It AUTO-DISCOVERS every in-repo
``tests`` directory (the main suite plus each core-lib's own tests) and runs
each one in its own subprocess, reporting a single non-zero exit on any
failure.

Two design points, both learned the hard way:

* Roots are auto-discovered, not hand-listed. A curated list silently went
  stale and dropped whole core-libs (codex / git / task / the provider libs /
  webserver) from every run — green but incomplete, the worst kind of
  test-suite bug. Discovery picks up a newly-extracted lib automatically.

* Each root runs in a SEPARATE process. Every core-lib ships a ``test_flow.py``
  (and similarly-named modules); discovering many roots in one process makes
  those collide in ``sys.modules`` ("module incorrectly imported … is this
  globally installed?"), which aborts discovery for the later roots. A
  subprocess per root isolates ``sys.modules`` completely — exactly like
  running that lib's tests by hand — while ``cwd=repo_root`` keeps the absolute
  ``<package>.<module>`` imports resolving.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# Pruned so discovery only ever walks THIS repo's tests, never a virtualenv's.
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
    test_roots = discover_test_roots(repo_root)
    print(f'Discovered {len(test_roots)} test roots:', flush=True)
    for relative in test_roots:
        print(f'  - {relative}', flush=True)

    failed: list[str] = []
    for relative in test_roots:
        start = repo_root / relative
        if not start.is_dir():
            continue
        print(f'\n=== {relative} ===', flush=True)
        proc = subprocess.run(
            [
                sys.executable, '-m', 'unittest', 'discover',
                '-s', str(start), '-p', 'test_*.py',
            ],
            cwd=str(repo_root),
        )
        if proc.returncode != 0:
            failed.append(relative)

    print('\n' + '=' * 64, flush=True)
    if failed:
        print(
            f'FAILED — {len(failed)}/{len(test_roots)} roots had failures: '
            + ', '.join(failed),
            flush=True,
        )
        return 1
    print(f'OK — all {len(test_roots)} test roots passed.', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
