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

// Ways to unstick a card. A healthy card has none of these.
export function stuckActions(step) {
  if (step.status === 'failed') {
    return [{ action: 'retry', label: 'Retry' }, { action: 'satisfy', label: 'Count as done' }];
  }
  if (isStuck(step)) {
    return [{ action: 'satisfy', label: 'Count as done' }];
  }
  return [];
}

// Plan states in which its steps are tracked and may be handed over.
export const LIVE_PLAN = ['approved', 'running', 'paused'];

// Whether the board may change this plan's tasks: one this kato drives, while
// its steps are tracked.
export function canEditPlan(plan) {
  return LIVE_PLAN.includes(plan?.status) && !isFollowed(plan);
}

// The server's rule for an approved plan: a task still waiting, never handed
// over (nor taken by someone), may change; any other is locked as it is.
export function canChangeStep(step) {
  return (step.status === 'blocked' || step.status === 'ready') && !step.released_at;
}

// An id for a task added by hand: "t" and the next number no task uses.
export function newStepId(steps) {
  const taken = new Set((steps || []).map((step) => step.id));
  let number = (steps || []).length + 1;
  while (taken.has(`t${String(number).padStart(2, '0')}`)) { number += 1; }
  return `t${String(number).padStart(2, '0')}`;
}

// What a card offers. A ready task can be started on its own (one at a time,
// even while the plan is paused or not yet running) — Plan: its agent plans
// first and codes once the plan is approved in its chat; Start: it implements
// right away. Either is the operator's pick for that task, whatever was chosen
// at Approve. A task still waiting
// can be counted as done when its work was merged some other way — which also
// frees the tasks waiting for it. Anything else: only a way out when stuck.
export function cardActions(step, planStatus) {
  if (LIVE_PLAN.includes(planStatus) && (step.status === 'ready' || step.status === 'blocked')) {
    const satisfy = {
      action: 'satisfy',
      label: 'Count as done',
      confirm: `Count ${stepCode(step)} as done? Its ticket moves to done, and the tasks `
        + 'waiting for it may start. Use it when its work was already merged some other way.',
    };
    if (step.status !== 'ready') { return [satisfy]; }
    return [
      {
        action: 'plan',
        label: 'Plan',
        tip: `Start ${stepCode(step)} in Plan mode: its agent plans first, and codes once you `
          + 'approve the plan in its chat.',
      },
      {
        action: 'start',
        label: 'Start',
        tip: `Start ${stepCode(step)} now: its agent implements right away, with no plan to approve first.`,
      },
      satisfy,
    ];
  }
  // A started task: the same Done as on the task itself (push, its pull
  // request, its ticket to Done) — one code path, wherever it is pressed.
  if (LIVE_PLAN.includes(planStatus) && step.task_id && STARTED_STEPS.includes(step.status)) {
    return [{
      action: 'done',
      label: 'Done',
      tip: `Done on ${stepCode(step)}, as on the task itself: push, open its pull request, move its `
        + 'ticket to Done. The card counts as done once that pull request is merged.',
    }];
  }
  return stuckActions(step);
}

const STARTED_STEPS = ['starting', 'active', 'in_review'];

// What a card says under its title. A step that is ready to be handed over says
// what it waits for: while the plan is PAUSED nothing is handed over — a restart
// pauses every running plan — so the server's "it will be retried" would promise
// what does not happen until Run is pressed (a card sat in Waiting that way
// while its tab was open in the working tasks).
export function stepNote(step, planStatus = '') {
  if (step.status === 'cancelled') { return 'cancelled'; }
  // Handed over (assigned) but nobody has started it yet. It sat in "In
  // progress" looking like work, while kato had not picked it up.
  if (step.status === 'starting' && !step.reason) { return 'handed over — not started yet'; }
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

// Its ticket's own state on the tracker ('' when it has no ticket or none was
// read yet) — so the board shows what the tracker says, word for word.
export function stepTaskState(step) {
  return step.task_id ? String(step.task_state || '') : '';
}

// The tracker state a started task sits in that this kato's workflow does not
// name ('' = none): read as "still to do", so its card never moves and the
// tasks after it never start — "Waiting Deploy" did that. The server lists
// them (``plan.unknown_states``); the card says so and offers to count it done.
const STARTED = ['starting', 'active', 'in_review'];

export function unknownStateOf(step, plan) {
  const state = stepTaskState(step).trim();
  if (!state || !STARTED.includes(step.status)) { return ''; }
  const unknown = (plan?.unknown_states || []).map((entry) => String(entry).toLowerCase());
  return unknown.includes(state.toLowerCase()) ? state : '';
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

// A task's split plan in a line, for its row in the tasks menu: the live plan
// (``plan``), else "Finished …" once its newest plan finished, else ''.
export function planRowLine(plan, finished = false) {
  if (plan) { return planStatusLine(plan); }
  return finished ? PLAN_STATUS_TEXT.finished : '';
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

// The options a task's model picker shows: every model kato offers, plus any
// ``picked`` id it no longer offers — shown as itself, so the picker never
// claims a model other than the one that will run.
export function modelOptions(models, ...picked) {
  const options = (models || []).map((model) => ({ id: model.id, label: model.label || model.id }));
  for (const id of picked) {
    if (id && !options.some((option) => option.id === id)) { options.push({ id, label: id }); }
  }
  return options;
}

// ``repo`` into every task ``mayChange`` allows — or out of all of them when
// each already has it. A task that may not change is left exactly as it is.
export function toggleRepoEverywhere(steps, repo, mayChange = () => true) {
  const open = (steps || []).filter(mayChange);
  const changed = withRepoEverywhere(open, repo, repoCoverage(open, repo).state !== 'all');
  let at = 0;
  return (steps || []).map((step) => {
    if (!mayChange(step)) { return step; }
    const next = changed[at];
    at += 1;
    return next;
  });
}
