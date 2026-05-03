"""Magic strings exchanged between kato and the agent.

Kept in one module so the prompt that *teaches* the agent to emit a
sentinel and the parser that *detects* it cannot drift apart. If you
rename a sentinel here, both the prompt and the detector update at
once because they import the same constant.
"""

from __future__ import annotations

# Wait-planning chat sessions hand control to the operator. When
# Claude considers the work done, it ends its final message with
# this exact token so kato knows to run the publish flow (push +
# open PR + move ticket to In Review) without the operator having
# to click the Done button manually.
#
# Angle brackets + uppercase + underscores: visually distinct from
# normal English prose so a stray mention in conversation ("the
# task is done") doesn't trigger the publish flow.
KATO_TASK_DONE_SENTINEL = '<KATO_TASK_DONE>'
