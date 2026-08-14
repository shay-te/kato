"""The composer's "Explain" mode — answer the question, change nothing.

Every other composer mode maps 1:1 onto a CLI ``--permission-mode``. Explain
does not: it is a kato-level mode that resolves into a permission mode PLUS a
tool split PLUS a prompt instruction. It exists because neither of the two
modes that look close actually does the job:

* **Plan** is read-only, but its whole contract is "explore, then propose a
  plan". Ask it a one-line question about the code and it produces a plan
  document — and kato then captures the ``ExitPlanMode`` event and pops the
  plan pane. Overkill for "what does this function do?".
* **Manual** answers questions, but it can still edit; the operator only finds
  out at the approval prompt, after the agent has decided to change code they
  only asked about.

Explain is therefore built from the read-only TOOL SPLIT rather than from plan
mode, so there is no plan machinery in the loop at all: the CLI refuses every
mutating tool, and the prompt says to answer in prose. The permission mode
stays ``default`` — with the mutating tools denied outright there is nothing
left for a permission prompt to gate, and asking for ``plan`` would drag the
plan behaviour back in through the front door.

The restriction is enforced by the CLI (tool denial), not by the instruction —
the instruction only shapes the ANSWER. A model that ignores the prose still
cannot write a file.
"""

from __future__ import annotations

from agent_core_lib.agent_core_lib.helpers.read_only_tools import (
    READ_ONLY_ALLOWED_TOOLS,
    READ_ONLY_DISALLOWED_TOOLS,
    is_read_only_tool_set,
)
from utils_core_lib.utils_core_lib.text_utils import normalized_text

# The composer's token for this mode. Deliberately NOT a CLI permission mode —
# it is resolved into one by ``resolve_explain_spawn``.
EXPLAIN_MODE = 'explain'

# What the spawn actually gets. ``default`` rather than ``plan``: see module
# docstring — the read-only guarantee comes from the tool denial below.
EXPLAIN_PERMISSION_MODE = 'default'

_EXPLAIN_INSTRUCTION = (
    'ANSWER-ONLY TURN — read the question below and answer it.\n'
    '- Do NOT produce a plan, a proposal, or a numbered list of steps you '
    'would take. This is a question, not a task assignment.\n'
    '- Do NOT edit, create, or delete anything. Every mutating tool is '
    'disabled for this turn, so attempting one only wastes a step.\n'
    '- Read whatever files you need to answer accurately, then reply in '
    'prose. Quote the relevant code.\n'
    '- Match the answer to the question: a small question gets a short '
    'answer. Do not expand it into a review of the surrounding code.'
)


def is_explain_mode(mode: object) -> bool:
    """Whether ``mode`` is the composer's Explain selection."""
    return normalized_text(mode).lower() == EXPLAIN_MODE


def resolve_explain_spawn(mode: object) -> dict[str, str]:
    """Spawn overrides for ``mode``.

    Returns ``{permission_mode, allowed_tools, disallowed_tools}`` for Explain,
    and empty strings for every other mode — an empty override means "use what
    the caller/defaults already resolved", so non-Explain spawns are untouched.
    """
    if not is_explain_mode(mode):
        return {'permission_mode': '', 'allowed_tools': '', 'disallowed_tools': ''}
    return {
        'permission_mode': EXPLAIN_PERMISSION_MODE,
        'allowed_tools': READ_ONLY_ALLOWED_TOOLS,
        'disallowed_tools': READ_ONLY_DISALLOWED_TOOLS,
    }


def explain_prompt(message: object) -> str:
    """``message`` prefixed with the answer-only instruction."""
    text = normalized_text(message)
    return f'{_EXPLAIN_INSTRUCTION}\n\n---\n\n{text}' if text else _EXPLAIN_INSTRUCTION


def session_is_in_explain_mode(session: object) -> bool:
    """Whether a LIVE session was spawned read-only.

    Lets a caller compare the operator's current selection against what is
    actually running without threading the kato-level mode through the
    transport, which only ever sees the resolved CLI flags.
    """
    return is_read_only_tool_set(getattr(session, 'disallowed_tools', ''))
