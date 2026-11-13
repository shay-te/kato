"""An interactive Grok chat: ``grok -p`` once per turn, resumed by id.

The per-turn machinery — one process per message, the stitched event log,
queued mid-turn messages, the session id learned from the first turn — is
shared by every per-turn CLI and lives in
``agent_core_lib.session.per_turn_session``. This module adds only what is
Grok's own:

* the prompt goes in a FILE (``--prompt-file``): headless Grok never reads
  stdin, and a long message must not hit a command-line length limit;
* every later turn adds ``--resume <session id>``;
* the turn's git access becomes Grok ``--deny`` rules, which hold even under
  ``--always-approve`` — so strict git is ENFORCED for Grok, not only asked;
* a task with several repositories runs in the folder that holds them all,
  since Grok has no ``--add-dir`` (``working_directory_utils.single_project_root``).
"""

from __future__ import annotations

import os
import shutil
import tempfile
from typing import Any

from agent_core_lib.agent_core_lib.session.per_turn_session import PerTurnCliSession
from agent_core_lib.agent_core_lib.session.turn_events import TurnEvent as SessionEvent
from grok_core_lib.grok_core_lib.session.wire_protocol import (
    SANDBOX_WORKSPACE,
    base_arguments,
    deny_rules,
    permission_arguments,
    translate_event,
)
from agent_core_lib.agent_core_lib.helpers.working_directory_utils import (
    single_project_root,
)

__all__ = ['SessionEvent', 'StreamingGrokSession']


class StreamingGrokSession(PerTurnCliSession):
    """A chat with Grok: one process per turn, one continuous event log."""

    DISPLAY_NAME = 'Grok'
    DEFAULT_BINARY = 'grok'
    DEFAULT_SANDBOX_MODE = SANDBOX_WORKSPACE
    PROMPT_ON_STDIN = False

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # One prompt file per chat, rewritten each turn. Turns never overlap
        # (the next starts only once the last process has exited), so the
        # file is never rewritten under a running CLI.
        self._prompt_dir = ''

    def _translate_event(self, raw: dict[str, Any]) -> list[dict[str, Any]]:
        return translate_event(raw)

    def _prompt_file(self, prompt: str) -> str:
        if not self._prompt_dir:
            self._prompt_dir = tempfile.mkdtemp(prefix='grok-chat-')
        path = os.path.join(self._prompt_dir, 'prompt.md')
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write(prompt)
        return path

    def _default_command(self, *, prompt: str, resume_id: str) -> list[str]:
        """``grok --prompt-file <file> [--resume <id>] …`` for one turn."""
        command = [*base_arguments(self._binary), '--prompt-file', self._prompt_file(prompt)]
        if resume_id:
            command.extend(['--resume', resume_id])
        else:
            # A resumed session keeps the working directory it started in.
            command.extend(['--cwd', single_project_root(self._cwd, self._additional_dirs)])
        if self._model:
            command.extend(['-m', self._model])
        command.extend(permission_arguments(sandbox=self._sandbox_mode))
        command.extend(deny_rules(self._turn_git_access))
        return command

    def terminate(self, grace_seconds: float = 1.0) -> None:
        super().terminate(grace_seconds=grace_seconds)
        if self._prompt_dir:
            shutil.rmtree(self._prompt_dir, ignore_errors=True)
            self._prompt_dir = ''
