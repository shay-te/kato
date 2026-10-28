"""The operator's standing instructions: their own text in every task's prompt.

Settings → Prompts → Standing instructions. Whatever the operator writes there
is appended to the agent's system prompt on every chat spawn — new, resumed,
or respawned after a restart — in the same ``--append-system-prompt`` that
carries the task-folder boundary and the lessons-file directive. A rule that
must hold for the whole task cannot live in a first message: a resumed chat
never receives it again and a long one summarises it away.

Read FRESH at every spawn (``live_setting``: settings.json, then the shell),
so a save applies to the next chat kato starts without restarting kato. A chat
that is already running keeps the system prompt it was started with.

Capped at ``STANDING_INSTRUCTIONS_MAX_CHARS``: the system prompt travels on
the command line, and Windows refuses one past 32,767 characters
(``[WinError 206]`` — the reason the lessons file is named by path instead of
pasted). The Settings save refuses a longer text; one hand-edited into
settings.json is cut at the cap, with a warning in the log.
"""

from __future__ import annotations

from kato_core_lib.helpers.kato_settings_store_utils import live_setting
from kato_core_lib.helpers.logging_utils import configure_logger

STANDING_INSTRUCTIONS_KEY = 'KATO_STANDING_INSTRUCTIONS'

#: About a page. Far below the Windows command-line limit even with the
#: boundary, lessons directive and plan guidance that share the same argument.
STANDING_INSTRUCTIONS_MAX_CHARS = 4000

_HEADER = (
    '# Standing instructions from the operator\n'
    '\n'
    'The operator wrote these for every task. Follow them alongside the task '
    'description; when the operator says otherwise in this chat, the chat '
    'wins.\n'
)

_logger = configure_logger(__name__)


def standing_instructions(env: dict | None = None) -> str:
    """The operator's text, at most ``STANDING_INSTRUCTIONS_MAX_CHARS``; '' when unset."""
    text = live_setting(STANDING_INSTRUCTIONS_KEY, env)
    if len(text) > STANDING_INSTRUCTIONS_MAX_CHARS:
        _logger.warning(
            '%s is %d characters; using the first %d. Shorten it in '
            'Settings → Prompts.',
            STANDING_INSTRUCTIONS_KEY, len(text), STANDING_INSTRUCTIONS_MAX_CHARS,
        )
        text = text[:STANDING_INSTRUCTIONS_MAX_CHARS].rstrip()
    return text


def with_standing_instructions(guidance: str, env: dict | None = None) -> str:
    """Kato's own system-prompt ``guidance``, then the operator's instructions.

    ``guidance`` comes back untouched when the operator has written none.
    """
    text = standing_instructions(env)
    if not text:
        return guidance
    block = f'{_HEADER}\n{text}'
    base = str(guidance or '').rstrip()
    return f'{base}\n\n{block}' if base else block
