// Pure helpers for the review loop's UI: wording, the "where is it now" step
// tracker, elapsed times. No React, no fetching — every surface (header chip,
// header button, tab badge, tab tooltip, the centre-pane view) renders from
// these, so they can never describe the same loop two different ways.
//
// The input is the loop's SUMMARY as the 5-second session poll carries it
// (``session.review_loop``): { loop_id, status, phase, round, max_rounds,
// phase_started_at, started_at, finished_at, waiting_for, reason, counts }.
// See review_loop_core_lib → ReviewLoopState.summary().
//
// A separate axis from the agent's own status (utils/agentStatus.js): nothing
// here says the agent is "working" — it says where the LOOP is.

export const REVIEW_LOOP_STATUS = Object.freeze({
  RUNNING: 'running',
  CLEAN: 'clean',
  MAX_ROUNDS: 'max_rounds',
  STUCK: 'stuck',
  STOPPED: 'stopped',
  FAILED: 'failed',
  INTERRUPTED: 'interrupted',
});

export const REVIEW_LOOP_PHASE = Object.freeze({
  WAITING_TO_REVIEW: 'waiting_to_review',
  REVIEWING: 'reviewing',
  WAITING_TO_SEND: 'waiting_to_send',
  AWAITING_FIX: 'awaiting_fix',
  DONE: 'done',
});

const PHASE_SHORT = {
  [REVIEW_LOOP_PHASE.WAITING_TO_REVIEW]: 'waiting',
  [REVIEW_LOOP_PHASE.REVIEWING]: 'reviewing',
  [REVIEW_LOOP_PHASE.WAITING_TO_SEND]: 'findings ready',
  [REVIEW_LOOP_PHASE.AWAITING_FIX]: 'fixing',
};

const PHASE_LONG = {
  [REVIEW_LOOP_PHASE.WAITING_TO_REVIEW]: 'waiting for the chat before reviewing',
  [REVIEW_LOOP_PHASE.REVIEWING]: 'an independent reviewer is reading the whole change',
  [REVIEW_LOOP_PHASE.WAITING_TO_SEND]: 'findings ready — they go to the chat when it is free',
  [REVIEW_LOOP_PHASE.AWAITING_FIX]: 'the main chat is fixing the findings',
};

// tone: good | warn | bad | neutral — the outcome's colour family.
const OUTCOMES = {
  [REVIEW_LOOP_STATUS.CLEAN]: { label: 'clean', sentence: 'finished clean', tone: 'good' },
  [REVIEW_LOOP_STATUS.MAX_ROUNDS]: { label: 'round limit', sentence: 'hit its round limit', tone: 'warn' },
  [REVIEW_LOOP_STATUS.STUCK]: { label: 'stuck', sentence: 'got stuck', tone: 'warn' },
  [REVIEW_LOOP_STATUS.STOPPED]: { label: 'stopped', sentence: 'was stopped', tone: 'neutral' },
  [REVIEW_LOOP_STATUS.FAILED]: { label: 'failed', sentence: 'failed', tone: 'bad' },
  [REVIEW_LOOP_STATUS.INTERRUPTED]: { label: 'interrupted', sentence: 'was interrupted', tone: 'neutral' },
};

const ROUND_OUTCOMES = {
  sent: 'sent to the chat',
  clean: 'clean',
  stuck: 'same issues as before',
  max_rounds: 'last round',
  stopped: 'stopped',
  failed: 'failed',
};

export const REVIEW_LOOP_EXPLAINER = (
  'Review loop — an independent reviewer reads the whole change and posts '
  + 'what it finds into this chat. The chat fixes each finding (with a test) '
  + 'or rejects it with evidence, and the next review is told those decisions. '
  + 'It repeats until a review comes back clean or the round limit is reached.'
);

// No loop yet: the header button opens the view, where the rounds are picked
// before anything starts.
export const REVIEW_LOOP_IDLE_TOOLTIP = `${REVIEW_LOOP_EXPLAINER} Click to pick how many rounds and start.`;

export const REVIEW_LOOP_EMPTY_TEXT = `${REVIEW_LOOP_EXPLAINER} Pick how many rounds above, then Start.`;

// "1 round", "5 rounds" — the round-limit picker's choices.
export function reviewLoopRoundsText(rounds) {
  return rounds === 1 ? '1 round' : `${rounds} rounds`;
}

// The view model for the loop tabs shown in the shared file-tab strip: for
// each ``{ taskId, tone }``, whether it is the active one and whether it leads
// the loop group (so the strip draws a divider before it, but only when file
// tabs precede it). Kept out of the component so the derivation is testable
// without mounting the strip.
// ``[{ taskId, tone }]`` for the open loop tabs — the tone is the loop's state
// colour (running / clean / …), read from each task's summary via ``loopFor``.
// Built here so the strip is handed ready-to-render entries, no lookups in JSX.
export function reviewLoopTabEntries(taskIds, loopFor) {
  return (taskIds || []).map((taskId) => {
    const chip = reviewLoopChip(loopFor(taskId), 0);
    return { taskId, tone: chip ? chip.tone : 'idle' };
  });
}

export function buildReviewLoopTabModels(loopTabs, { activeTaskId = '', hasFileTabs = false } = {}) {
  return (loopTabs || []).map((loop, index) => ({
    taskId: loop.taskId,
    tone: loop.tone,
    active: loop.taskId === activeTaskId,
    groupStart: index === 0 && hasFileTabs,
  }));
}

export function isReviewLoopRunning(loop) {
  return loop?.status === REVIEW_LOOP_STATUS.RUNNING;
}

export function reviewLoopOutcome(loop) {
  return OUTCOMES[loop?.status] || null;
}

// The blocking count — what keeps a loop going (BLOCKER + MAJOR). Findings an
// earlier decision settled are counted apart (``SETTLED``), never in here.
export function blockingCount(counts) {
  if (!counts) { return 0; }
  return Number(counts.BLOCKER || 0) + Number(counts.MAJOR || 0);
}

// "2 blocking · 1 minor · 3 nit · 1 settled"; "no issues" when there is nothing.
export function countsText(counts) {
  if (!counts) { return ''; }
  const parts = [];
  const blocking = blockingCount(counts);
  if (blocking) { parts.push(`${blocking} blocking`); }
  if (counts.MINOR) { parts.push(`${counts.MINOR} minor`); }
  if (counts.NIT) { parts.push(`${counts.NIT} nit`); }
  if (counts.SETTLED) { parts.push(`${counts.SETTLED} settled`); }
  return parts.length ? parts.join(' · ') : 'no issues';
}

// A finding that keeps the loop going: BLOCKER / MAJOR, not already settled by
// an earlier round's decision.
export function isBlockingFinding(finding) {
  const blockingSeverity = finding?.severity === 'BLOCKER' || finding?.severity === 'MAJOR';
  return blockingSeverity && !finding.settled_by;
}

// What the chat did with a finding it was sent — the round's decision ledger.
// tone: good | warn | neutral | bad, like the loop's outcomes.
const DECISIONS = {
  fixed: { label: 'Fixed', tone: 'good' },
  rejected: { label: 'Rejected', tone: 'warn' },
  out_of_scope: { label: 'Out of scope', tone: 'neutral' },
  unanswered: { label: 'Not answered', tone: 'bad' },
};

// { label, tone, note } for a sent finding; null until its fix turn ended.
export function findingDecision(round, finding) {
  const response = (round?.responses || []).find((item) => item.finding_id === finding?.id);
  if (!response) { return null; }
  const decision = DECISIONS[response.decision] || DECISIONS.unanswered;
  const notes = [];
  if (response.test) { notes.push(`test: ${response.test}`); }
  if (response.decision === 'fixed' && !response.test) { notes.push('no test named'); }
  if (response.evidence) { notes.push(response.evidence); }
  const disputed = response.decision === 'rejected' || response.decision === 'out_of_scope';
  if (disputed && !response.evidence) { notes.push('no evidence given, so not settled'); }
  return { label: decision.label, tone: decision.tone, note: notes.join(' · ') };
}

// 45s · 4m 12s · 1h 3m
export function formatElapsed(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  if (total < 60) { return `${total}s`; }
  const minutes = Math.floor(total / 60);
  if (minutes < 60) { return `${minutes}m ${total % 60}s`; }
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

function phaseElapsed(loop, nowSeconds) {
  const since = Number(loop?.phase_started_at || 0);
  return since ? formatElapsed(nowSeconds - since) : '';
}

// The header chip: "2/5 · fixing · 4m 12s" while running; the outcome after.
// null when the task has never had a loop.
export function reviewLoopChip(loop, nowSeconds) {
  if (!loop) { return null; }
  if (isReviewLoopRunning(loop)) {
    const elapsed = phaseElapsed(loop, nowSeconds);
    const parts = [`${loop.round || 1}/${loop.max_rounds}`, PHASE_SHORT[loop.phase] || 'starting'];
    if (elapsed) { parts.push(elapsed); }
    return { text: parts.join(' · '), tone: 'running', running: true };
  }
  const outcome = reviewLoopOutcome(loop);
  if (!outcome) { return null; }
  return { text: outcome.label, tone: outcome.tone, running: false };
}

// One sentence for tooltips and the tab hover card.
export function reviewLoopSentence(loop, nowSeconds) {
  if (!loop) { return ''; }
  if (isReviewLoopRunning(loop)) {
    const elapsed = phaseElapsed(loop, nowSeconds);
    const waiting = loop.waiting_for ? ` (${loop.waiting_for})` : '';
    const forHowLong = elapsed ? ` · ${elapsed}` : '';
    return `Round ${loop.round || 1} of ${loop.max_rounds} · ${PHASE_LONG[loop.phase] || 'starting'}${waiting}${forHowLong}`;
  }
  const outcome = reviewLoopOutcome(loop);
  if (!outcome) { return ''; }
  const after = loop.round ? ` after ${loop.round} round${loop.round === 1 ? '' : 's'}` : '';
  const reason = loop.reason ? ` — ${loop.reason}` : '';
  return `The review loop ${outcome.sentence}${after}${reason}`;
}

const STEP_ORDER = ['review', 'send', 'fix', 'next'];
const CURRENT_STEP = {
  [REVIEW_LOOP_PHASE.WAITING_TO_REVIEW]: 'review',
  [REVIEW_LOOP_PHASE.REVIEWING]: 'review',
  [REVIEW_LOOP_PHASE.WAITING_TO_SEND]: 'send',
  [REVIEW_LOOP_PHASE.AWAITING_FIX]: 'fix',
};

// The live "where is it now" tracker for the current round:
//   [✓ Reviewed (3 blocking · 2 minor)] → [✓ Sent to the chat] →
//   [● Fixing in the main chat · 4m 12s] → [○ Review round 3]
// Each step: { key, label, state: 'done' | 'current' | 'todo', detail }.
// Empty for a loop that is not running.
export function reviewLoopSteps(loop, nowSeconds) {
  if (!isReviewLoopRunning(loop)) { return []; }
  const current = CURRENT_STEP[loop.phase] || 'review';
  const currentIndex = STEP_ORDER.indexOf(current);
  const elapsed = phaseElapsed(loop, nowSeconds);
  const round = loop.round || 1;
  const isLastRound = round >= loop.max_rounds;
  const labels = {
    review: currentIndex > 0 ? 'Reviewed' : reviewingLabel(loop),
    send: currentIndex > 1 ? 'Sent to the chat' : 'Send the findings to the chat',
    fix: 'Fixing in the main chat',
    next: isLastRound ? 'Loop ends' : `Review round ${round + 1}`,
  };
  return STEP_ORDER.map((key, index) => {
    let state = 'todo';
    if (index < currentIndex) { state = 'done'; }
    if (index === currentIndex) { state = 'current'; }
    return { key, label: labels[key], state, detail: stepDetail(key, state, loop, elapsed) };
  });
}

function reviewingLabel(loop) {
  return loop.phase === REVIEW_LOOP_PHASE.REVIEWING ? 'Reviewing the whole change' : 'Waiting to review';
}

function stepDetail(key, state, loop, elapsed) {
  if (state === 'current') {
    const waiting = loop.waiting_for ? `${loop.waiting_for} · ` : '';
    return `${waiting}${elapsed}`;
  }
  if (key === 'review' && state === 'done') { return countsText(loop.counts); }
  return '';
}

export function roundOutcomeLabel(round) {
  return ROUND_OUTCOMES[round?.outcome] || 'in progress';
}

// "api/app.py:42 (run)" — where a finding is.
export function findingLocation(finding) {
  const path = [finding.repository, finding.file].filter(Boolean).join('/');
  const line = path && finding.line ? `:${finding.line}` : '';
  const symbol = finding.symbol ? ` (${finding.symbol})` : '';
  return `${path}${line}${symbol}`;
}

// Changes whenever the loop moves to a new step — the moment the full loop
// (every round) is worth re-fetching for the view, and no other.
export function reviewLoopSignature(loop) {
  if (!loop) { return ''; }
  return [loop.loop_id, loop.status, loop.phase, loop.round].join(':');
}
