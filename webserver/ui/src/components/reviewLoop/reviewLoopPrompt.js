// Recognise the review loop's findings message in the chat transcript.
//
// The loop posts its findings into the main chat as a user turn, so without
// this the transcript would show kato's message as "You asked" — words the
// operator never wrote. The message carries a fixed header line, built in
// Python (kato_core_lib/helpers/review_loop_guidance.py →
// REVIEW_LOOP_FINDINGS_HEADER = 'Kato review loop — round {round} of
// {max_rounds}'); a Python test pins this pattern to that template.
//
// Matched on ANY line, not only the first: a message that respawns the chat
// reaches the agent behind kato's own workspace preamble, so the header is
// often not the opening line of the turn the transcript replays.
export const REVIEW_LOOP_HEADER_PATTERN = /^Kato review loop — round (\d+) of (\d+)$/m;

// The loop's OTHER messages: the main chat's self-check, the test run, failing
// tests sent back to fix, and the "continue" nudge a resumed loop sends a chat
// it was cut off waiting for. Same pinning as above, against
// REVIEW_LOOP_STAGE_HEADER = 'Kato review loop — {stage}'.
export const REVIEW_LOOP_STAGE_PATTERN = /^Kato review loop — (self-check \d+ of \d+|run the tests|fix the failing tests|continue round \d+)$/m;

// ``{ round, maxRounds }`` for a findings message, ``{ stage }`` for a
// self-check / test message, or null for anything the operator wrote.
export function parseReviewLoopPrompt(text) {
  const value = String(text || '');
  const match = REVIEW_LOOP_HEADER_PATTERN.exec(value);
  if (match) { return { round: Number(match[1]), maxRounds: Number(match[2]) }; }
  const stage = REVIEW_LOOP_STAGE_PATTERN.exec(value);
  return stage ? { stage: stage[1] } : null;
}

// The sticky prompt's label: kato's loop message says so; anything else is
// what the operator asked.
export function stickyPromptLabel(text) {
  const loop = parseReviewLoopPrompt(text);
  if (!loop) { return 'You asked'; }
  if (loop.stage) { return `Kato · review loop · ${loop.stage}`; }
  return `Kato · review loop round ${loop.round}/${loop.maxRounds}`;
}
