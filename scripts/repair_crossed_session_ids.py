#!/usr/bin/env python3
"""Repair session records where one chat id got filed under two backends.

THE DAMAGE THIS UNDOES

Switching a task's chat to another agent PARKS the outgoing one: the record
moves to the new backend and its session id is cleared, but the outgoing
subprocess is deliberately left running so switching back resumes it. Several
code paths would then write that still-running agent's id onto the record —
which by then belongs to a different agent. The next switch-back parked the
foreign id under the other backend's key.

The result is a record where the SAME id appears under two backends. It is not
a cosmetic mislabel: opening the wrong tab hands one CLI the other's id.
Claude looks for a transcript in a cwd-keyed directory and Codex looks for a
rollout by id in one flat store, so neither finds the other's conversation.
The chat opens blank and the history looks lost.

WHICH COPY IS THE REAL ONE

Not guessable from the record. The corruption has two orientations depending
on whether the operator has switched back yet:

    fresh leak     agent_backend=codex,  agent_session_id=<claude id>
                   chats_by_backend={'claude': {<claude id>, previous...}}
    after switch   agent_backend=claude, agent_session_id=<claude id>
                   chats_by_backend={'codex':  {<claude id>}}

In the first the ACTIVE entry is the bogus one; in the second the PARKED entry
is. An earlier version of this script assumed the active copy was always
genuine, and so — in the first orientation, the one a live leak actually
produces — deleted the operator's real Claude chat along with its
``previous_session_ids`` and kept the corrupt id.

So this does not guess. A session id is a fact on disk: Claude's transcripts
live under ``~/.claude/projects``, Codex's rollouts under ``~/.codex/sessions``.
The owner is whichever backend's store actually contains that id. The copy
under every OTHER backend is the duplicate, and only that copy is removed.

When ownership cannot be established — the id is in no store (pruned or
adopted from a machine this one cannot see) or somehow in both — the record is
REPORTED AND SKIPPED. Refusing to act beats deleting the wrong conversation.

Dry-run by default: it prints what it would do and changes nothing. Pass
``--apply`` to write, which first copies each file it edits to ``<name>.bak``.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Backends that keep a local session store. Ownership can only be decided for
# these; anything else has no on-disk fact to check against.
_RESOLVABLE_BACKENDS = ('claude', 'codex')


def default_sessions_dir() -> Path:
    return Path.home() / '.kato' / 'sessions'


def _key(value: object) -> str:
    return str(value or '').strip().lower()


def backends_holding(record: dict, session_id: str) -> list[str]:
    """Every backend on ``record`` whose chat is ``session_id``.

    Includes the ACTIVE backend, because the active entry is the bogus one as
    often as a parked entry is.
    """
    wanted = str(session_id or '').strip()
    if not wanted:
        return []
    holders = []
    if str(record.get('agent_session_id') or '').strip() == wanted:
        holders.append(_key(record.get('agent_backend')))
    chats = record.get('chats_by_backend') or {}
    if isinstance(chats, dict):
        holders.extend(
            _key(backend) for backend, chat in chats.items()
            if str((chat or {}).get('agent_session_id') or '').strip() == wanted
        )
    return sorted({h for h in holders if h})


def crossed_ids(record: dict) -> list[str]:
    """Session ids this record files under more than one backend."""
    candidates = {str(record.get('agent_session_id') or '').strip()}
    chats = record.get('chats_by_backend') or {}
    if isinstance(chats, dict):
        candidates.update(
            str((chat or {}).get('agent_session_id') or '').strip()
            for chat in chats.values()
        )
    return sorted(
        sid for sid in candidates
        if sid and len(backends_holding(record, sid)) > 1
    )


def _store_owner(session_id: str) -> str:
    """Which backend's on-disk store actually contains ``session_id``.

    Returns '' when no single backend claims it — the ambiguous case the
    caller must refuse to act on.
    """
    from agent_backend_core_lib.agent_backend_core_lib.client.session_index_factory import (  # noqa: E501
        list_adoptable_sessions,
    )

    owners = []
    for backend in _RESOLVABLE_BACKENDS:
        try:
            rows = list_adoptable_sessions(backend)
        except Exception:  # pragma: no cover — a broken store is not fatal
            continue
        if any(
            str(getattr(row, 'agent_session_id', '') or '').strip() == session_id
            for row in rows
        ):
            owners.append(backend)
    return owners[0] if len(owners) == 1 else ''


def strip_id_from(record: dict, session_id: str, backends: list[str]) -> dict:
    """Remove ``session_id`` from each backend in ``backends``.

    A parked entry is dropped whole. The ACTIVE entry cannot be dropped — the
    record must keep a backend — so its id is cleared instead, which is the
    truth: that agent never had this conversation.
    """
    chats = dict(record.get('chats_by_backend') or {})
    for backend in backends:
        if _key(record.get('agent_backend')) == backend:
            record['agent_session_id'] = ''
        chats.pop(backend, None)
    record['chats_by_backend'] = chats
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--sessions-dir', type=Path, default=default_sessions_dir(),
        help='where the per-task records live (default: ~/.kato/sessions)',
    )
    parser.add_argument(
        '--apply', action='store_true',
        help='actually write the repair (default: dry run)',
    )
    options = parser.parse_args(argv)

    root: Path = options.sessions_dir
    if not root.is_dir():
        print(f'no session records at {root}')
        return 0

    # Counted separately on purpose. Folding them into one counter meant it
    # only ever incremented on the --apply path, so a DRY RUN printed "no
    # crossed session ids found." directly under a list of crossed session
    # ids — the one message guaranteed to stop someone re-running with
    # --apply.
    detected = 0  # records that have a repairable crossing
    applied = 0  # records actually rewritten on disk
    skipped = 0
    for path in sorted(root.glob('*.json')):
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            print(f'skipped {path.name}: unreadable ({exc})')
            continue
        if not isinstance(record, dict):
            continue

        changed = False
        for session_id in crossed_ids(record):
            holders = backends_holding(record, session_id)
            owner = _store_owner(session_id)
            print(f'{path.name}: id {session_id} filed under {holders}')
            if not owner:
                print('    SKIPPED — no store claims this id, so which copy is '
                      'genuine cannot be established. Left untouched.')
                skipped += 1
                continue
            duplicates = [b for b in holders if b != owner]
            if not duplicates:
                continue
            print(f'    {owner} owns it (its store has the transcript); '
                  f'removing the copy under {duplicates}')
            strip_id_from(record, session_id, duplicates)
            changed = True

        if not changed:
            continue
        detected += 1
        if not options.apply:
            continue
        shutil.copy2(path, path.with_suffix(path.suffix + '.bak'))
        path.write_text(
            json.dumps(record, indent=2) + '\n', encoding='utf-8',
        )
        applied += 1
        print(f'    repaired (backup at {path.name}.bak)')

    if not detected and not skipped:
        print('no crossed session ids found.')
    elif not options.apply:
        print(f'\n{detected} record(s) would be repaired'
              f'{f", {skipped} skipped as ambiguous" if skipped else ""}. '
              'Re-run with --apply to fix.')
    else:
        print(f'\nrepaired {applied} record(s)'
              f'{f", skipped {skipped} as ambiguous" if skipped else ""}.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
