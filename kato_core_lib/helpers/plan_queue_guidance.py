"""Kato's own rules for splitting a plan into tasks.

``plan_queue_core_lib`` writes the generic proposer instructions (format, ids,
limits); this is the product guidance kato adds on top — how kato runs a step,
so the proposer splits for that.
"""

PLAN_SPLIT_GUIDANCE = (
    'How these tasks will run:\n'
    '- Each task gets its own fresh clone of the repositories, its own branch and '
    'its own pull request into the default branch. A task that depends on another '
    'starts only after that one is merged, so it sees the merged code.\n'
    '- Make every task a change that can be reviewed and merged on its own and '
    'leaves the code working.\n'
    '- Tasks with no dependency between them may run at the same time: do not give '
    'two such tasks the same files to change, or their pull requests will conflict. '
    'If two tasks must touch the same files, make one depend on the other.\n'
    '- Put tests with the change they cover, in the same task.'
)


# The split is asked for in the task's own chat, where the operator watches it
# and steers it. These frame the queue's request there.
PLAN_SPLIT_CHAT_HEADER = 'Kato plan queue · split the plan'
PLAN_SPLIT_CHAT_CLOSING = (
    'Answer here in the chat, in your reply text — not through ExitPlanMode — and change no '
    'files. The operator reads it here and may ask you to change the split: answer each change '
    'with the whole updated <task-split> block (kato reads the newest one into the plan board).'
)


# A plan sub-task's agent asked its operator something; the operator put the
# question to the chat the plan was split in, which holds the plan's reasons.
# The header line lets the UI show it as kato's message, not "You asked"
# (``planQueuePrompt.js`` — a test pins the two together).
PLAN_QUESTION_CHAT_HEADER = 'Kato plan queue · question from {task_id}'


def plan_question_message(task_id: str, questions_text: str) -> str:
    return (
        f'{PLAN_QUESTION_CHAT_HEADER.format(task_id=task_id)}\n\n'
        f'{task_id}, one of the tasks this plan was split into, is asking its operator:\n\n'
        f'{questions_text}\n\n'
        'Answer from what you know of the plan and this code: for each question, the option '
        'you would pick (or your own answer) and why, in a few lines. Do not change any files — '
        'the operator reads your answer and replies to the task themselves.'
    )
