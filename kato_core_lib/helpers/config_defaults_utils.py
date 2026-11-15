"""The default each setting falls back to, read from kato's own config files.

Every setting kato reads is a ``${oc.env:KEY,"default"}`` in a config yaml — the
default is written there and nowhere else. The settings forms showed a hint
from a hand-kept list instead, and for Jira it named YouTrack's values ("State",
"To Verify") where Jira's are "status" and "In Review". Read from the yaml, a
field's placeholder is the value an empty field really means.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ENV_DEFAULT = re.compile(r'\$\{oc\.env:([A-Z0-9_]+),"([^"]*)"\}')


def config_defaults(roots: tuple[Path, ...] | None = None) -> dict[str, str]:
    """``{SETTING_KEY: default}`` from every config yaml (kato's and each
    library's). A key with no written default is not in it."""
    return dict(_read_defaults(tuple(roots) if roots is not None else _config_roots()))


def _config_roots() -> tuple[Path, ...]:
    return tuple(sorted(path for path in _REPO_ROOT.glob('*/*/config') if path.is_dir())) + (
        _REPO_ROOT / 'kato_core_lib' / 'config',
    )


@lru_cache(maxsize=4)
def _read_defaults(roots: tuple[Path, ...]) -> tuple[tuple[str, str], ...]:
    found: dict[str, str] = {}
    for root in roots:
        for path in sorted(Path(root).rglob('*.yaml')):
            try:
                text = path.read_text(encoding='utf-8')
            except OSError:
                continue
            for key, default in _ENV_DEFAULT.findall(text):
                found.setdefault(key, default)
    return tuple(found.items())
