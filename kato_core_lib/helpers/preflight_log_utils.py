"""The workspace-preparation log, as the operator reads it in the chat.

Every time kato prepares a task it appends to ``<task>/.kato-preflight.log``, and
the chat replays the whole file. It used to write a line for EVERY repository on
EVERY preparation — "cloning 3/26: … (already on disk, reusing)" and then
"✓ cloned 3/26: …" for a repository nothing had been done to — so reopening an
old task after a restart put fifty lines of nothing into the chat, and every
restart added fifty more. The operator: "i dont need to see this redundant".

Now only real work is written: the repositories actually cloned, and every
failure. A preparation with nothing to clone writes nothing at all.

Logs written before that still hold the old lines, so ``visible_preflight_entries``
leaves them out when a log is replayed. The file itself is never rewritten.
"""

from __future__ import annotations

import re

_PREPARING_PREFIX = 'preparing workspace'
# The old per-repository lines. ``<position>/<total>: <repository>`` is the key
# that ties a reuse announcement to the "✓ cloned" line written for it later.
_LEGACY_REUSED = re.compile(r'^cloning (\d+/\d+: .+) \(already on disk, reusing\)$')
_CLONED = re.compile(r'^✓ cloned (\d+/\d+: .+)$')
_LEGACY_READY = re.compile(r'^✓ all \d+ repository\(ies\) cloned — starting agent$')
_READY = re.compile(r'^✓ cloned \d+ repository\(ies\) — starting agent$')


def preparing_message(to_clone: int, total: int) -> str:
    reused = total - to_clone
    already = f' — {reused} already on disk' if reused else ''
    return f'{_PREPARING_PREFIX}: cloning {to_clone} of {total} repository(ies){already}'


def cloning_message(position: int, count: int, repository_id: str) -> str:
    return f'cloning {position}/{count}: {repository_id}'


def cloned_message(position: int, count: int, repository_id: str) -> str:
    return f'✓ cloned {position}/{count}: {repository_id}'


def clone_failed_message(repository_id: str, error: object) -> str:
    return f'✗ clone failed: {repository_id}: {error}'


def ready_message(cloned: int) -> str:
    return f'✓ cloned {cloned} repository(ies) — starting agent'


def _is_bookend(text: str) -> bool:
    return (
        text.startswith(_PREPARING_PREFIX)
        or bool(_LEGACY_READY.match(text))
        or bool(_READY.match(text))
    )


def _without_reuse_noise(run: list[tuple[float, str]]) -> list[tuple[float, str]]:
    """One preparation's lines minus its reuse announcements, or ``[]`` when
    reuse was all it did."""
    reused = set()
    for _epoch, text in run:
        match = _LEGACY_REUSED.match(text)
        if match:
            reused.add(match.group(1))
    kept = []
    for epoch, text in run:
        if _LEGACY_REUSED.match(text):
            continue
        cloned = _CLONED.match(text)
        if cloned and cloned.group(1) in reused:
            continue
        kept.append((epoch, text))
    if all(_is_bookend(text) for _epoch, text in kept):
        return []
    return kept


def visible_preflight_entries(entries) -> list[tuple[float, str]]:
    """The ``(epoch, message)`` entries worth showing, oldest first.

    Each preparation starts with its "preparing workspace" line. Within one,
    the old reuse lines and the "✓ cloned" lines written for those same reused
    repositories are dropped; a preparation left with nothing but its opening
    and closing lines is dropped whole. Failures are always kept, and lines
    before the first preparation are passed through untouched.
    """
    visible: list[tuple[float, str]] = []
    run: list[tuple[float, str]] = []
    for epoch, text in entries or []:
        if str(text).startswith(_PREPARING_PREFIX):
            visible.extend(_without_reuse_noise(run))
            run = [(epoch, text)]
        elif run:
            run.append((epoch, text))
        else:
            visible.append((epoch, text))
    visible.extend(_without_reuse_noise(run))
    return visible
