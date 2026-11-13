"""Discover the Grok CLI's supported ``--reasoning-effort`` levels at runtime.

Mirrors every other transport's ``helpers.effort_levels`` name for name. The
levels are read from ``grok --help``, where the CLI's argument parser lists a
flag's allowed values as ``[possible values: none, minimal, …]``, so the set
tracks the installed version. The static list is only the fallback for when
the binary cannot be run (offline tests, a missing CLI). Which levels a given
MODEL accepts is narrower, and Grok reports a mismatch itself.
"""

from __future__ import annotations

import re
import subprocess
import threading

# Fallback only — the levels Grok Build documents for ``--reasoning-effort``.
FALLBACK_EFFORT_LEVELS = ('none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max')

_FLAG = '--reasoning-effort'
_cache: dict[str, tuple[str, ...]] = {}
_cache_lock = threading.Lock()


def discover_effort_levels(binary: str = 'grok', timeout: float = 10.0) -> list[str]:
    """The ``--reasoning-effort`` levels the CLI advertises. Never empty, never raises."""
    key = str(binary or 'grok').strip() or 'grok'
    with _cache_lock:
        if key in _cache:
            return list(_cache[key])
    discovered = _parse_effort_levels_from_help(key, timeout)
    levels = tuple(discovered) if discovered else FALLBACK_EFFORT_LEVELS
    with _cache_lock:
        _cache[key] = levels
    return list(levels)


def _parse_effort_levels_from_help(binary: str, timeout: float) -> list[str] | None:
    try:
        proc = subprocess.run(
            [binary, '--help'],
            capture_output=True, text=True, timeout=timeout, check=False,
            # Never the operator's terminal: given a TTY a CLI can go raw, and a
            # timeout kill leaves it raw — Ctrl+C then can't stop the host.
            stdin=subprocess.DEVNULL,
        )
    except Exception:
        return None
    return _parse_effort_levels_text(f'{proc.stdout or ""}\n{proc.stderr or ""}')


def _parse_effort_levels_text(help_text: str) -> list[str] | None:
    """The values listed for ``--reasoning-effort`` in ``help_text``, or None.

    Only the flag's own block is read — up to the next option — so another
    flag's ``possible values`` can never be mistaken for these.
    """
    start = help_text.find(_FLAG)
    if start < 0:
        return None
    block = help_text[start + len(_FLAG):]
    next_flag = re.search(r'\n\s+-{1,2}[a-zA-Z]', block)
    if next_flag:
        block = block[:next_flag.start()]
    match = re.search(r'\[possible values: ([^\]]*)\]', block)
    if not match:
        return None
    levels = [part.strip().lower() for part in match.group(1).split(',')]
    levels = [level for level in levels if re.fullmatch(r'[a-z][a-z0-9_-]*', level)]
    return levels or None


def reset_effort_levels_cache() -> None:
    """Clear the discovery cache (tests / a CLI upgrade mid-process)."""
    with _cache_lock:
        _cache.clear()
