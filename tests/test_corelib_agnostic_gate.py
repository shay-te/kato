"""ENFORCED agnostic gate — no core-lib (except kato_core_lib) may say "kato".

This gate exists because the agnostic pass keeps REGRESSING during feature work:
a feature adds a ``KATO_*`` env read or a ``kato`` comment into a shared lib, the
lib stops being standalone/open-source-ready, and nobody notices until the next
audit. This test turns that into a BUILD FAILURE.

THE RULE (see AGENTS.md → "Core-libs stay kato-free"):
    Every ``KATO_*`` variable and the ``kato`` brand live ONLY in ``kato_core_lib``.
    Every other core-lib reads GENERIC names (``AGENT_*``, ``CLAUDE_SESSIONS_ROOT``,
    ...) or takes values via constructor/params; ``kato_core_lib`` owns the
    ``KATO_*`` config and BRIDGES it (e.g.
    ``kato_core_lib._export_agent_env_from_kato_config`` exports
    ``AGENT_IGNORED_REPOSITORY_FOLDERS`` from ``KATO_IGNORED_REPOSITORY_FOLDERS``).

HOW THE GATE WORKS — a ratchet:
    Each lib has a CEILING of kato-containing lines. ``0`` = fully agnostic
    (LOCKED). A non-zero ceiling = known debt that must only ever go DOWN. The
    test fails if a lib EXCEEDS its ceiling — i.e. a feature re-introduced kato.
    When you clean a lib below its ceiling, LOWER the number here (never raise it).
"""
from __future__ import annotations

import os
import re
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_KATO = re.compile('kato', re.IGNORECASE)

# Max kato-containing lines per lib (source + tests). 0 = locked agnostic.
# kato_core_lib is intentionally absent — it OWNS the kato brand/config.
# Ratchet rule: you may only ever DECREASE these numbers.
_MAX_KATO_LINES = {
    # ---- fully agnostic — LOCKED at 0, do not regress ----
    'agent_backend_core_lib': 0,
    'agent_core_lib': 0,
    'bitbucket_core_lib': 0,
    'claude_core_lib': 0,
    'codex_core_lib': 0,
    'git_core_lib': 0,
    'github_core_lib': 0,
    'gitlab_core_lib': 0,
    'jira_core_lib': 0,
    'openrouter_core_lib': 0,
    'task_core_lib': 0,
    # ---- known debt — clean these up; lower the number, never raise it ----
    'agent_provider_contracts': 18,
    'openhands_core_lib': 14,
    'provider_client_base': 52,
    'repository_core_lib': 2,
    'sandbox_core_lib': 403,
    'security_scanner_core_lib': 33,
    'vcs_provider_contracts': 2,
    'workspace_core_lib': 8,
    'youtrack_core_lib': 34,
}


def _count_kato_lines(lib: str) -> int:
    pkg = os.path.join(_ROOT, lib, lib)
    if not os.path.isdir(pkg):
        pkg = os.path.join(_ROOT, lib)
    total = 0
    for dirpath, _dirs, files in os.walk(pkg):
        if '__pycache__' in dirpath:
            continue
        for name in files:
            if not name.endswith('.py'):
                continue
            path = os.path.join(dirpath, name)
            with open(path, encoding='utf-8', errors='replace') as fh:
                total += sum(1 for line in fh if _KATO.search(line))
    return total


class CoreLibAgnosticGateTests(unittest.TestCase):
    def test_no_corelib_exceeds_its_kato_ceiling(self) -> None:
        offenders = []
        for lib, ceiling in sorted(_MAX_KATO_LINES.items()):
            actual = _count_kato_lines(lib)
            if actual > ceiling:
                offenders.append((lib, actual, ceiling))
        self.assertEqual(
            offenders, [],
            '\n\nA core-lib gained "kato" references. KATO_* env vars and the '
            '"kato" brand belong ONLY in kato_core_lib — use a GENERIC name in the '
            'lib and BRIDGE it from kato_core_lib (see AGENTS.md "Core-libs stay '
            'kato-free"). Offenders (lib: found > allowed):\n'
            + '\n'.join(f'  - {lib}: {a} > {c}' for lib, a, c in offenders)
            + '\n\nDo NOT raise the ceiling to make this pass — remove the kato ref.',
        )

    def test_ceilings_are_not_stale(self) -> None:
        """If a lib was cleaned below its ceiling, tighten the ratchet.

        Soft nudge: fails only when a ceiling is loose by a clear margin, so a
        cleanup PR is reminded to lock in its win (lower the number / set 0).
        """
        loose = []
        for lib, ceiling in sorted(_MAX_KATO_LINES.items()):
            if ceiling == 0:
                continue
            actual = _count_kato_lines(lib)
            if actual < ceiling:
                loose.append((lib, actual, ceiling))
        self.assertEqual(
            loose, [],
            '\n\nThese libs are now BELOW their kato ceiling — lower the number in '
            '_MAX_KATO_LINES to lock in the cleanup (ratchet only goes down):\n'
            + '\n'.join(f'  - {lib}: now {a}, ceiling still {c}' for lib, a, c in loose),
        )


if __name__ == '__main__':
    unittest.main()
