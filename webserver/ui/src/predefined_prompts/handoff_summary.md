HANDOFF SUMMARY — this chat is being replaced by a fresh one, and your summary is the only thing the new chat will know about it.

Write a handoff summary of this whole conversation for the agent that continues this task in the new chat. Do not change any files and do not run anything for this — answer from what you already know.

Put the entire summary between these two marker lines, spelled exactly like this, with nothing of the summary outside them:

<kato-handoff>
(the summary goes here)
</kato-handoff>

Cover, in this order:
1. Goal — what the task is and what the operator actually wants, including everything they clarified or changed along the way.
2. Decisions — what was agreed and why, and the approaches that were ruled out.
3. Done so far — the changes made, with file paths, and what was verified (which tests ran, and their results).
4. Current state — what is finished, what is half-done, and anything uncommitted or unpushed.
5. Open questions — what is still waiting on the operator.
6. Next steps — the concrete next actions, in order.
7. Rules and gotchas — constraints the operator set, commands that matter, and mistakes not to repeat.

Be specific: paths, names, commands and numbers, not generalities. Keep it as short as it can be without leaving out anything the next agent would otherwise have to rediscover.
