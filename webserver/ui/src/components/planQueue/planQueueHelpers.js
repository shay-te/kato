// What the plan board shows, derived from a plan — pure, so it is tested alone.
//
// These are the QUEUE's states (waiting / in progress / in review / waiting for
// merge / done): a separate axis from the agent status. Nothing here reads a
// session or decides whether an agent is working — that is deriveAgentStatus()
// alone (CLAUDE.md, "Agent status has EXACTLY ONE derivation").

export const BOARD_COLUMNS = [
  { id: 'waiting', label: 'Waiting', statuses: ['blocked', 'ready'] },
  { id: 'progress', label: 'In progress', statuses: ['starting', 'active'] },
  { id: 'review', label: 'In review', statuses: ['in_review'] },
  { id: 'merge', label: 'Waiting for merge', statuses: ['awaiting_merge'] },
  { id: 'done', label: 'Done', statuses: ['satisfied'] },
];

// A failed or cancelled step stays in the column where it stopped.
const STOPPED_COLUMN = {
  pr_declined: 'merge',
  task_gone: 'progress',
  launch_failed: 'progress',
  launch_refused: 'waiting',
  create_failed: 'waiting',
};

export const PLAN_STATUS_TEXT = {
  proposing: 'The agent is splitting the plan in this task\'s chat…',
  propose_failed: 'The split could not be proposed',
  proposed: 'Review the proposed tasks',
  approved: 'Approved — press Run to start',
  running: 'Running',
  paused: 'Paused',
  finished: 'Finished — every task is done',
  cancelled: 'Stopped',
};

const NO_PR_PREFIX = 'no pull request found';

export function columnOf(step) {
  const column = BOARD_COLUMNS.find((entry) => entry.statuses.includes(step.status));
  if (column) { return column.id; }
  if (step.status === 'failed') { return STOPPED_COLUMN[step.reason_code] || 'waiting'; }
  return step.released_at ? 'progress' : 'waiting';
}

export function boardColumns(plan) {
  const steps = plan?.steps || [];
  return BOARD_COLUMNS.map((column) => ({
    ...column,
    steps: steps.filter((step) => columnOf(step) === column.id),
  }));
}

// The card's code: its task id once it has one, else the step's own id.
export function stepCode(step) {
  return step.task_id || step.id;
}

export function isStuck(step) {
  return step.status === 'failed'
    || (step.status === 'awaiting_merge' && String(step.reason || '').startsWith(NO_PR_PREFIX));
}

// The only actions a card offers: ways to unstick it. A healthy card has none.
export function stuckActions(step) {
  if (step.status === 'failed') {
    return [{ action: 'retry', label: 'Retry' }, { action: 'satisfy', label: 'Count as done' }];
  }
  if (isStuck(step)) {
    return [{ action: 'satisfy', label: 'Count as done' }];
  }
  return [];
}

// What a card says under its title. A step that is ready to be handed over says
// what it waits for: while the plan is PAUSED nothing is handed over — a restart
// pauses every running plan — so the server's "it will be retried" would promise
// what does not happen until Run is pressed (a card sat in Waiting that way
// while its tab was open in the working tasks).
export function stepNote(step, planStatus = '') {
  if (step.status === 'cancelled') { return 'cancelled'; }
  if (step.status === 'ready' && planStatus === 'paused') {
    return step.released_at
      ? 'paused — press Run to hand it over again'
      : 'paused — press Run to start it';
  }
  return String(step.reason || '');
}

// Who has the step's task, while it is someone's to do ('' once done, or nobody).
export function stepWorker(step) {
  if (!step.worker || step.status === 'satisfied') { return ''; }
  return `with ${step.worker}`;
}

// A plan another kato drives: shown here, changed only there (or after Take over).
export function isFollowed(plan) {
  return plan?.mode === 'followed';
}

// Per-repository pull request state, for the chips on a card.
export function prChips(step) {
  return (step.merge?.repos || [])
    .filter((repo) => repo.state && repo.state !== 'unknown')
    .map((repo) => ({ repository: repo.repository_id, state: repo.state, url: repo.url || '' }));
}

// Which header controls a plan in this state offers.
export function headerActions(status) {
  if (status === 'approved' || status === 'paused') { return ['run', 'stop']; }
  if (status === 'running') { return ['pause', 'stop']; }
  return [];
}

// The newest plan split from ``taskId`` — or one of whose steps runs as it —
// an unfinished one first.
export function planSummaryFor(summaries, taskId) {
  const key = String(taskId || '').toLowerCase();
  const ofTask = (plan) => String(plan.source_task_id).toLowerCase() === key
    || (plan.task_ids || []).some((id) => String(id).toLowerCase() === key);
  const mine = (summaries || []).filter(ofTask);
  if (!mine.length) { return null; }
  const open = mine.filter((plan) => plan.status !== 'finished' && plan.status !== 'cancelled');
  const pool = open.length ? open : mine;
  return pool.reduce((newest, plan) => ((plan.updated_at || 0) >= (newest.updated_at || 0) ? plan : newest));
}

// "Running · 3/17 done" — where a plan is, in one line: the toolbar button's
// tooltip and the task tab's hover card say it the same way.
export function planStatusLine(summary) {
  if (!summary) { return ''; }
  const progress = progressText(summary);
  return `${PLAN_STATUS_TEXT[summary.status] || summary.status}${progress ? ` · ${progress}` : ''}`;
}

// "3/5 done" — or '' before there are steps.
export function progressText(summary) {
  const total = summary?.step_count || 0;
  if (!total) { return ''; }
  return `${summary.counts?.satisfied || 0}/${total} done`;
}

// "Plan board" once there is a board to show; "Split plan" before (no plan,
// one being split or reviewed, or one stopped).
export function buttonLabel(summary) {
  return summary && summary.status !== 'cancelled' && isBoardStatus(summary.status) ? 'Plan board' : 'Split plan';
}

const PROPOSAL_STATUSES = new Set(['proposing', 'propose_failed', 'proposed']);

// Past the proposal: the plan shows as the board.
export function isBoardStatus(status) {
  return !!status && !PROPOSAL_STATUSES.has(status);
}

// The toolbar button's dot: 'running' while an agent proposes or the plan
// runs, 'warn' while a split waits for the operator's review, '' otherwise.
export function buttonDot(summary) {
  const status = summary?.status;
  if (status === 'proposing' || status === 'running') { return 'running'; }
  if (status === 'proposed') { return 'warn'; }
  return '';
}

// Each step's round: 1 for a step that waits for nothing, else one more than
// the latest round among the steps it waits for — the order the queue will
// actually run them in. 0 for a step caught in a circle (the validator says
// why when the split is saved).
export function stepRounds(steps) {
  const byId = new Map((steps || []).map((step) => [step.id, step]));
  const rounds = new Map();
  function roundOf(id, visiting) {
    if (rounds.has(id)) { return rounds.get(id); }
    if (visiting.has(id)) { return 0; }
    visiting.add(id);
    let round = 1;
    for (const dep of byId.get(id).depends_on || []) {
      if (!byId.has(dep)) { continue; }
      const depRound = roundOf(dep, visiting);
      round = depRound === 0 ? 0 : Math.max(round, depRound + 1);
      if (round === 0) { break; }
    }
    visiting.delete(id);
    rounds.set(id, round);
    return round;
  }
  for (const id of byId.keys()) { roundOf(id, new Set()); }
  return rounds;
}

// "17 tasks · 5 rounds · 4 start right away" — the whole split in one line.
export function roundsSummary(steps, rounds) {
  const count = (steps || []).length;
  const values = [...rounds.values()];
  const deepest = Math.max(0, ...values);
  const first = values.filter((round) => round === 1).length;
  const tasks = `${count} task${count === 1 ? '' : 's'}`;
  if (!count) { return tasks; }
  const startText = `${first} ${first === 1 ? 'starts' : 'start'} right away`;
  return `${tasks} · ${deepest} round${deepest === 1 ? '' : 's'} · ${startText}`;
}

// How much of the split works in ``repo``: { state: 'all' | 'some' | 'none', count }.
export function repoCoverage(steps, repo) {
  const list = steps || [];
  const count = list.filter((step) => (step.repositories || []).includes(repo)).length;
  let state = 'some';
  if (count === 0) { state = 'none'; }
  if (list.length > 0 && count === list.length) { state = 'all'; }
  return { state, count };
}

// The steps with ``repo`` added to every one (``everywhere``) or taken out of
// every one — what "every task works in" does in one click. A task that
// already worked there keeps its own order.
export function withRepoEverywhere(steps, repo, everywhere) {
  return (steps || []).map((step) => {
    const repositories = step.repositories || [];
    if (!everywhere) { return { ...step, repositories: repositories.filter((entry) => entry !== repo) }; }
    return { ...step, repositories: repositories.includes(repo) ? [...repositories] : [...repositories, repo] };
  });
}
