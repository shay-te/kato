"""An interactive Codex chat: ``codex exec`` once per turn, resumed by id.

The per-turn machinery — one process per message, the stitched event log,
queued mid-turn messages, the session id learned from the first turn — is
shared by every per-turn CLI and lives in
``agent_core_lib.session.per_turn_session``. This module adds only what is
Codex's own:

* the first turn runs ``codex exec`` and every later one
  ``codex exec resume <thread_id>`` — a sub-subcommand, not a flag;
* the prompt goes on STDIN;
* the sandbox's writable directories ride in a ``-c`` override on EVERY turn
  (see ``_writable_roots``).
"""

from __future__ import annotations

import json
import os
from typing import Any

from agent_core_lib.agent_core_lib.helpers.command_floor import GIT_ACCESS_LOCAL
from agent_core_lib.agent_core_lib.session.per_turn_session import PerTurnCliSession
from agent_core_lib.agent_core_lib.session.turn_events import TurnEvent as SessionEvent
from codex_core_lib.codex_core_lib.session.wire_protocol import translate_event

__all__ = ['SessionEvent', 'StreamingCodexSession']


class StreamingCodexSession(PerTurnCliSession):
    """A chat with Codex: one process per turn, one continuous event log."""

    DISPLAY_NAME = 'Codex'
    DEFAULT_BINARY = 'codex'
    DEFAULT_SANDBOX_MODE = 'workspace-write'
    PROMPT_ON_STDIN = True
    #: Printed by ``codex exec`` on every run; never why a turn died.
    STDERR_NOISE = ('Reading prompt from stdin',)

    def _translate_event(self, raw: dict[str, Any]) -> list[dict[str, Any]]:
        return translate_event(raw)

    def _writable_roots(self) -> list[str]:
        """Every directory beyond ``cwd`` this turn may write to, on EVERY turn.

        ``--add-dir`` reaches only a FRESH turn: ``codex exec resume`` rejects
        it, and a resumed turn does not keep the first turn's either — proven on
        codex-cli 0.166 with the repositories outside every temp folder (which
        the sandbox lets anyone write): turn one wrote into the task's second
        repository, the resumed turn two could not. A config override is
        accepted by both, so the extra directories — and, at local git access,
        each repository's ``.git`` — ride in one. It replaces any
        ``writable_roots`` in the user's own Codex config for the turn.
        """
        roots = [directory for directory in self._additional_dirs if directory]
        if self._turn_git_access == GIT_ACCESS_LOCAL:
            roots += [git_dir for git_dir in self._git_writable_roots() if git_dir not in roots]
        return roots

    def _git_writable_roots(self) -> list[str]:
        """Each workspace root's own ``.git``, which the sandbox keeps read-only.

        Codex's workspace-write sandbox makes ``<root>/.git`` read-only in every
        writable root — staging, let alone committing, fails there with
        "Unable to create .git/index.lock: Operation not permitted". Local git
        access hands exactly those directories back, nothing wider.
        """
        roots: list[str] = []
        for root in (self._cwd, *self._additional_dirs):
            git_dir = os.path.join(root, '.git') if root else ''
            if git_dir and os.path.isdir(git_dir) and git_dir not in roots:
                roots.append(git_dir)
        return roots

    def _default_command(self, *, prompt: str, resume_id: str) -> list[str]:
        """``codex exec [resume <id>] --json`` for one turn."""
        del prompt  # delivered on stdin, not as an argument
        command = [self._binary, 'exec']
        if resume_id:
            # A sub-subcommand, not a flag — and it accepts a RESTRICTED option
            # set: --sandbox / -C / --add-dir are rejected here. A resumed turn
            # does NOT inherit the first turn's --add-dir (see
            # ``_writable_roots``), so those go in a config override.
            command.extend(['resume', resume_id])
        command.extend(['--json', '--skip-git-repo-check'])
        if self._model:
            command.extend(['-m', self._model])
        writable = self._writable_roots()
        if writable:
            command.extend([
                '-c', 'sandbox_workspace_write.writable_roots=' + json.dumps(writable),
            ])
        if not resume_id:
            command.extend(['--sandbox', self._sandbox_mode])
            if self._cwd:
                command.extend(['-C', self._cwd])
            for directory in self._additional_dirs:
                command.extend(['--add-dir', directory])
        return command
