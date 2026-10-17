"""How the split request names the plan and the operator's files: by where
they are, not by pasting them.

The split is asked for in the task's chat, which reads files itself — so the
request points at ``plan.md`` / each split file / the split's README instead of
copying their text in (the chat stays short, and the operator watches the
agent read them). The queue still keeps its own snapshot: a step's brief is
the file's text as it was when the split was asked for.
"""

from __future__ import annotations

from kato_core_lib.helpers.plan_queue_guidance import PLAN_SPLIT_GUIDANCE


class KatoSplitWording(object):
    def wrap_untrusted(self, text: str, *, source: str) -> str:
        if source.startswith('plan of '):
            return 'It is plan.md in the task folder — read it there.'
        if source.startswith('overview of '):
            return 'Read the README.md beside these files: it gives their order.'
        return f'Read {source} in the task folder.'

    def guidance(self) -> str:
        return PLAN_SPLIT_GUIDANCE
