"""An interactive OpenCode chat: ``opencode run`` once per turn, resumed by id.

The per-turn machinery — one process per message, the stitched event log,
queued mid-turn messages, the session id learned from the first turn — is
shared by every per-turn CLI and lives in
``agent_core_lib.session.per_turn_session``. This module adds only what is
OpenCode's own:

* the prompt goes on STDIN (``opencode run`` with no message reads it all);
* every later turn adds ``--session <session id>``;
* OpenCode prints no terminal event, so a clean exit completes the turn;
* the turn's git access becomes OpenCode permission rules, passed inline in
  the environment — so strict git is ENFORCED for OpenCode, not only asked;
* ``$PWD`` names the project: a task with several repositories runs in the
  folder that holds them all, since OpenCode has no ``--add-dir``.
"""

from __future__ import annotations

import os
from typing import Any

from agent_core_lib.agent_core_lib.helpers.working_directory_utils import (
    single_project_root,
)
from agent_core_lib.agent_core_lib.session.per_turn_session import PerTurnCliSession
from agent_core_lib.agent_core_lib.session.turn_events import TurnEvent as SessionEvent
from opencode_core_lib.opencode_core_lib.helpers.cli_generation import cli_generation
from opencode_core_lib.opencode_core_lib.session.wire_protocol import (
    MODE_WORKSPACE,
    model_arguments,
    run_arguments,
    run_environment,
    translate_event,
)

__all__ = ['SessionEvent', 'StreamingOpenCodeSession']


class StreamingOpenCodeSession(PerTurnCliSession):
    """A chat with OpenCode: one process per turn, one continuous event log."""

    DISPLAY_NAME = 'OpenCode'
    DEFAULT_BINARY = 'opencode'
    DEFAULT_SANDBOX_MODE = MODE_WORKSPACE
    PROMPT_ON_STDIN = True
    CLEAN_EXIT_ENDS_TURN = True

    def _translate_event(self, raw: dict[str, Any]) -> list[dict[str, Any]]:
        return translate_event(raw)

    def _default_command(self, *, prompt: str, resume_id: str) -> list[str]:
        """``opencode run --format json [--session <id>] …`` for one turn."""
        del prompt  # delivered on stdin, not as an argument
        generation = cli_generation(self._binary)
        command = run_arguments([self._binary], generation)
        if resume_id:
            command.extend(['--session', resume_id])
        command.extend(model_arguments(self._model, '', generation))
        return command

    def _turn_env(self) -> dict[str, str]:
        env = dict(super()._turn_env() or os.environ)
        env.update(run_environment(
            single_project_root(self._cwd, self._additional_dirs),
            git_access=self._turn_git_access,
            mode=self._sandbox_mode,
        ))
        return env
