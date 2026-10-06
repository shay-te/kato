"""Kato's wording for the review loop (the loop itself is ``review_loop_core_lib``).

The generic lib writes two prompts — the reviewer's and the findings posted
into the chat — and takes the product-specific parts from here, the same way
the agent transports take ``KATO_AGENT_GUIDANCE``.
"""

from __future__ import annotations

from kato_core_lib.data_layers.data.sentinels import KATO_TASK_DONE_SENTINEL

# The FIRST line of every message the loop posts into a task's chat. The UI
# keys off it to show the message as kato's ("Kato · review loop round 2/5")
# instead of "You asked" — keep it in step with
# ``webserver/ui/src/components/reviewLoop/reviewLoopPrompt.js`` (a test pins
# the two together).
REVIEW_LOOP_FINDINGS_HEADER = 'Kato review loop — round {round} of {max_rounds}'

# For the agent fixing the findings. The done marker matters most: printed
# here it would start kato's publish flow in the middle of a review cycle.
REVIEW_LOOP_FIXER_GUIDANCE = (
    f'- This is one round of an automated review loop, not the end of the task: '
    f'kato reviews the change again when you finish. Never print '
    f'{KATO_TASK_DONE_SENTINEL} in this reply.'
)

# For the independent reviewer.
REVIEW_LOOP_REVIEWER_GUIDANCE = (
    'If a lessons file is named in your system prompt, its rules are binding '
    'project rules too: a change that breaks one is a finding.'
)
