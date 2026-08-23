// How expensive a chat has become, in one place.
//
// The context meter answers "how close am I to the wall". It cannot answer
// "what is this costing me": every turn re-reads the WHOLE context, so a chat
// sitting at 490k of a 1M window shows a healthy "51% left" while charging
// 490k tokens on every single turn. Measured on a real 9,073-turn session:
// ~490k average per turn, 4.4 BILLION cache-read tokens billed, and nothing
// on screen said a word about it.
//
// This is a DIFFERENT question from the context meter's "% left" and gets its
// own indicator: a chat can sit at a comfortable 51% left and still re-read
// half a million tokens on every single turn.
//
// The baseline is the chat's own first measured turn — system prompt, project
// instructions, injected docs — i.e. what starting over would cost. Both the
// composer meter and the task tab derive from this module so "expensive"
// means exactly one thing in the UI.

// A chat costing this many times a fresh one is worth restarting. The growth
// is dominated by tool output a new chat simply would not carry, so by 5x
// most of what each turn pays for is history the task has moved past, and by
// 10x almost all of it is.
export const MODERATE_MULTIPLE = 5;
export const EXPENSIVE_MULTIPLE = 10;

export function chatCostMultiple(usedTokens, baselineTokens) {
  const used = Number(usedTokens);
  const baseline = Number(baselineTokens);
  if (!Number.isFinite(used) || !Number.isFinite(baseline)) { return 0; }
  if (baseline <= 0 || used <= baseline) { return 0; }
  return used / baseline;
}

// 'safe' | 'moderate' | 'expensive', or '' when we have no reading. Empty is
// NOT the same as safe: with no baseline we cannot tell a cheap chat from a
// ruinous one, and a green light we did not earn is worse than no light.
export function chatCostLevel(usedTokens, baselineTokens) {
  const used = Number(usedTokens);
  const baseline = Number(baselineTokens);
  if (!Number.isFinite(used) || !Number.isFinite(baseline)) { return ''; }
  if (used <= 0 || baseline <= 0) { return ''; }
  const multiple = chatCostMultiple(used, baseline);
  if (multiple >= EXPENSIVE_MULTIPLE) { return 'expensive'; }
  if (multiple >= MODERATE_MULTIPLE) { return 'moderate'; }
  return 'safe';
}

export function formatCostMultiple(multiple) {
  return multiple >= 10 ? String(Math.round(multiple)) : multiple.toFixed(1);
}

// Token counts, humanised. Lives here because both indicators that report
// token numbers (the context meter and the cost dot) need exactly this, and
// two copies of it is how they drift.
export function formatTokens(count) {
  if (count >= 1_000_000) {
    const millions = count / 1_000_000;
    return `${millions >= 10 ? Math.round(millions) : millions.toFixed(1)}M`;
  }
  if (count >= 1_000) { return `${Math.round(count / 1_000)}k`; }
  return String(count);
}
