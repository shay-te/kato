// Recognise the plan queue's split request in the chat transcript.
//
// The queue asks for a split in the task's main chat as a user turn, so without
// this the transcript would show kato's request as "You asked" — words the
// operator never wrote. The request carries a fixed header line, built in
// Python (kato_core_lib/helpers/plan_queue_guidance.py → PLAN_SPLIT_CHAT_HEADER
// = 'Kato plan queue · split the plan'); a Python test pins this pattern to it.
//
// Matched on ANY line, not only the first: a request that respawns the chat
// reaches the agent behind kato's own workspace preamble.
export const PLAN_SPLIT_HEADER_PATTERN = /^Kato plan queue · split the plan$/m;

export const PLAN_SPLIT_PROMPT_LABEL = 'Kato · plan split';

export function isPlanSplitPrompt(text) {
  return PLAN_SPLIT_HEADER_PATTERN.test(String(text || ''));
}

// A plan sub-task's question, put to this chat by the operator
// (plan_queue_guidance.py → PLAN_QUESTION_CHAT_HEADER = 'Kato plan queue ·
// question from {task_id}'; pinned by the same Python test). Kato's message,
// labelled with the sub-task that asked — never "You asked".
export const PLAN_QUESTION_HEADER_PATTERN = /^Kato plan queue · question from (\S+)$/m;

// The asking sub-task's id ('' = not such a message).
export function planQuestionFrom(text) {
  const match = PLAN_QUESTION_HEADER_PATTERN.exec(String(text || ''));
  return match ? match[1] : '';
}
