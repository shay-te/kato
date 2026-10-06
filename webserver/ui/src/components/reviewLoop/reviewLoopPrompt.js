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

export function parseReviewLoopPrompt(text) {
  const match = REVIEW_LOOP_HEADER_PATTERN.exec(String(text || ''));
  if (!match) { return null; }
  return { round: Number(match[1]), maxRounds: Number(match[2]) };
}

// The sticky prompt's label: kato's loop message says so; anything else is
// what the operator asked.
export function stickyPromptLabel(text) {
  const loop = parseReviewLoopPrompt(text);
  return loop ? `Kato · review loop round ${loop.round}/${loop.maxRounds}` : 'You asked';
}
