"""Kato-specific actionable refusal guidance for out-of-scope paths.

Appended (via ``extra_refusal_guidance``) after the generic strict
workspace-boundary block that ``agent_core_lib`` renders. It lives here,
not in ``agent_core_lib``, because it names Kato product concepts —
``kato:repo`` tags, the ticket platform, the Files-tab "Sync
repositories" action. Kato hands it to the agent client factory at
construction so ``agent_core_lib`` / ``claude_core_lib`` stay
product-agnostic.

Path-agnostic on purpose: the generic block already lists the allowed
paths, so the template references "the workspace paths listed above"
instead of re-embedding them — which lets this stay a plain string the
factory can thread through unchanged.
"""
from __future__ import annotations

KATO_WORKSPACE_REFUSAL_GUIDANCE = (
    'WHEN YOU MUST REFUSE A PATH-OUT-OF-SCOPE REQUEST — USE THIS TEMPLATE:\n'
    'Do not just say "I can\'t". The operator needs to know WHAT you were '
    'spawned with and HOW to widen the scope. Reply with this template, '
    'filling in the missing-path name:\n'
    '\n'
    '   I can\'t write to `<requested-path>` because this session was\n'
    '   spawned with sandbox access to only the workspace paths listed\n'
    '   above.\n'
    '\n'
    '   To widen scope:\n'
    '   1. **Check the task tags** for a matching `kato:repo:<id>`.\n'
    '      - **Tag is missing:** add it on the task in your ticket\n'
    '        platform (YouTrack/Jira), then click "Sync repositories"\n'
    '        in the Files tab. Once kato confirms the clone landed,\n'
    '        close + reopen this chat tab.\n'
    '      - **Tag is already there:** close + reopen this chat tab.\n'
    '        I was spawned with the OLD set of repos in my sandbox;\n'
    '        a fresh spawn picks up the current tags.\n'
    '   2. For multi-repo changes, repeat for each repo.\n'
    '\n'
    '   Once my session restarts with the broader sandbox, I can\n'
    '   make the change.\n'
    '\n'
    'Use the template verbatim (substituting the real path). It gives the '
    'operator a complete diagnosis instead of forcing them to guess why a '
    'tag-already-on-the-task is being ignored.'
)


# The git-request channel. Same injection pattern and same reason as the
# block above: it names kato product concepts (the task folder layout, the
# Done button), so it lives here rather than in agent_core_lib, and is
# threaded to the agent client as a parameter.
#
# It exists because a refusal with no next step is what an operator reads
# as kato being broken: the agent hits the branch/publish floor and reports
# "the orchestration layer forbids me from running any git command", when
# kato would happily perform the operation on request.
from kato_core_lib.helpers.git_request import agent_guidance_text

KATO_GIT_REQUEST_GUIDANCE = agent_guidance_text()


# The single value kato threads into every agent spawn as
# ``workspace_refusal_guidance`` / ``extra_refusal_guidance``.
#
# Composed ONCE here rather than concatenated at each call site: the runner
# has two spawn paths and the one-shot client a third, and a block added to
# one of them but not the others is invisible until an agent misbehaves on
# exactly the path that missed it.
KATO_AGENT_GUIDANCE = (
    f'{KATO_WORKSPACE_REFUSAL_GUIDANCE}\n\n{KATO_GIT_REQUEST_GUIDANCE}'
)
