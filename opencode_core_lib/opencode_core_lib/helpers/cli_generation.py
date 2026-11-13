"""Which OpenCode generation is installed: V1 (``opencode-ai``) or V2 (``@opencode/cli``).

Both ship the same ``opencode`` binary and are both maintained, but their
command lines differ where this transport depends on them:

* V2's ``run`` reuses an already-running shared background service unless
  given ``--standalone`` — and that service ignores the run's environment,
  which is where this transport puts its permission rules. V1 runs its server
  in-process and has no such flag (an unknown flag is an error);
* reasoning effort is ``--variant <name>`` on V1 and ``model#<name>`` on V2;
* exporting a session is ``opencode export`` on V1, ``opencode session export``
  on V2.

Decided once per binary from ``opencode --version``. A version that cannot be
read is taken as V2 on purpose: on V1 that fails loudly (an unknown flag),
while the opposite mistake would run V2 WITHOUT ``--standalone`` and silently
lose the permission rules.
"""

from __future__ import annotations

import re
import subprocess
import threading

GENERATION_V1 = 1
GENERATION_V2 = 2
_VERSION = re.compile(r'(\d+)\.(\d+)\.(\d+)')
_TIMEOUT_SECONDS = 15.0

_cache: dict[str, int] = {}
_cache_lock = threading.Lock()


def cli_generation(binary: str = 'opencode') -> int:
    """``GENERATION_V1`` or ``GENERATION_V2`` for ``binary``. Never raises."""
    key = str(binary or 'opencode').strip() or 'opencode'
    with _cache_lock:
        if key in _cache:
            return _cache[key]
    generation = _generation_from_version(_version_text(key))
    with _cache_lock:
        _cache[key] = generation
    return generation


def _version_text(binary: str) -> str:
    try:
        proc = subprocess.run(
            [binary, '--version'],
            capture_output=True, text=True, timeout=_TIMEOUT_SECONDS, check=False,
            # Never the operator's terminal: given a TTY a CLI can go raw, and a
            # timeout kill leaves it raw.
            stdin=subprocess.DEVNULL,
        )
    except Exception:
        return ''
    return f'{proc.stdout or ""}\n{proc.stderr or ""}'


def _generation_from_version(text: str) -> int:
    match = _VERSION.search(str(text or ''))
    if match and int(match.group(1)) == 1:
        return GENERATION_V1
    return GENERATION_V2


def generation_flags(generation: int) -> list[str]:
    """Flags every server-backed command needs on this generation."""
    return ['--standalone'] if generation >= GENERATION_V2 else []


def reset_cli_generation_cache() -> None:
    """Forget the detected generations (tests / a CLI upgrade mid-process)."""
    with _cache_lock:
        _cache.clear()
