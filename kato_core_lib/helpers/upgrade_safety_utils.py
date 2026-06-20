"""Verify the data that must SURVIVE an agent-CLI upgrade is intact.

Upgrading the agent CLI (``npm i -g @anthropic-ai/claude-code``, pip for
codex) replaces ONLY the binary — the operator's chats, sessions, and
credentials live elsewhere and are untouched by the swap:

* chats / transcripts  → ``~/.claude/projects/<encoded-cwd>/<sid>.jsonl``
* kato session records → ``~/.kato/sessions/<id>.json``
* host credentials     → ``ANTHROPIC_API_KEY`` / ``CLAUDE_CODE_OAUTH_TOKEN``
                          env, or the CLI's ``~/.claude.json`` login
* docker-mode creds    → the ``kato-claude-config`` Docker volume

This reports their presence so ``kato doctor`` can confirm — before AND
after an upgrade — that nothing was lost. (Re-running before/after and
comparing the counts is the concrete "make sure" check.)
"""

from __future__ import annotations

import os
from pathlib import Path

_CRED_ENV_KEYS = ('ANTHROPIC_API_KEY', 'CLAUDE_CODE_OAUTH_TOKEN')
# CLI login credential files on the host (relative to $HOME).
_CLAUDE_LOGIN_FILES = ('.claude.json', '.claude/.credentials.json')


def _projects_root(env: dict) -> Path:
    """Claude transcript root — honours ``CLAUDE_SESSIONS_ROOT`` from the
    passed env (matching where --resume reads), else the canonical resolver."""
    override = str(env.get('CLAUDE_SESSIONS_ROOT') or '').strip()
    if override:
        return Path(override).expanduser()
    try:
        from claude_core_lib.claude_core_lib.session.index import (
            default_sessions_root,
        )
        return default_sessions_root()
    except Exception:
        return Path.home() / '.claude' / 'projects'


def _count_glob(root: Path, pattern: str, recursive: bool, cap: int = 100000) -> int:
    if not root.is_dir():
        return 0
    count = 0
    try:
        it = root.rglob(pattern) if recursive else root.glob(pattern)
        for _ in it:
            count += 1
            if count >= cap:
                break
    except OSError:
        pass
    return count


def persistence_health(env: dict | None = None, home: Path | None = None) -> dict:
    """Presence + counts of chats, kato sessions, and host credentials.

    Never raises — a missing directory reads as ``count 0`` / ``present
    False`` rather than an error. ``home`` is injectable for tests.
    """
    env = os.environ if env is None else env
    home = home or Path.home()

    projects = _projects_root(env)
    chat_count = _count_glob(projects, '*.jsonl', recursive=True)

    sessions_dir = home / '.kato' / 'sessions'
    session_count = _count_glob(sessions_dir, '*.json', recursive=False)

    sources: list[str] = []
    for key in _CRED_ENV_KEYS:
        if str(env.get(key) or '').strip():
            sources.append(key)
    for rel in _CLAUDE_LOGIN_FILES:
        if (home / rel).exists():
            sources.append('~/' + rel)

    return {
        'chats': {
            'present': chat_count > 0, 'count': chat_count, 'dir': str(projects),
        },
        'sessions': {
            'present': session_count > 0, 'count': session_count,
            'dir': str(sessions_dir),
        },
        'host_credentials': {'present': bool(sources), 'sources': sources},
    }


def persistence_health_lines(env: dict | None = None, home: Path | None = None) -> list[str]:
    """Human-readable lines for the ``kato doctor`` / boot output."""
    health = persistence_health(env, home=home)
    cred = health['host_credentials']
    cred_str = ', '.join(cred['sources']) if cred['sources'] else 'none on host'
    return [
        ' kato — data preserved across an agent CLI upgrade',
        f"  chats (transcripts)   : {health['chats']['count']} in {health['chats']['dir']}",
        f"  kato sessions         : {health['sessions']['count']} in {health['sessions']['dir']}",
        f'  host credentials      : {cred_str}',
        '  note: a CLI upgrade replaces only the binary — the above live in '
        '~/.claude and ~/.kato (docker-mode creds in the kato-claude-config '
        'volume) and are NOT touched. Never delete those dirs or run '
        '`docker volume rm kato-claude-config`.',
    ]
