import { useCallback, useState } from 'react';
import { cx } from '../../utils/cx.js';
import {
  controlPlanQueue,
  discardPlanQueue,
  proposePlanQueue,
  retryPlanQueueProposal,
} from '../../api.js';
import { useBusyAction } from '../../hooks/useBusyAction.js';
import { useDelayedFlag } from '../../hooks/useDelayedFlag.js';
import {
  usePlanQueueDetail,
  usePlanQueueShared,
  usePlanQueueSplitFolders,
  usePlanQueueSummary,
} from '../../hooks/usePlanQueue.js';
import PlanBoard from './PlanBoard.jsx';
import PlanProposal from './PlanProposal.jsx';
import { PLAN_STATUS_TEXT, isBoardStatus } from './planQueueHelpers.js';
import { planAction } from './planQueueActions.js';
import { planQueueChanges } from './planQueueViewStore.js';

// The split happens in the task's chat — the operator watches it there and
// tells the agent what to change; the tab shows the result.
const IN_CHAT_NOTE = 'Watch it in the chat, and tell the agent there what to change — '
  + 'its split shows up here when it answers. Kato tells you when the tasks are ready to review.';

// A task's plan queue, as a tab in the centre strip: split its plan (or take a
// split the operator already wrote as files), approve the split, then watch
// the board. A tab, never a popup over the window: a proposal takes minutes,
// and nothing else may wait on it.
//
// Before a split exists it offers the split folders found in the task folder
// and a fresh split of plan.md; while an agent proposes it says so; a proposal
// is reviewed in PlanProposal; after approval it is the read-only PlanBoard.
export default function PlanQueueView({ taskId, onOpenTask = null }) {
  const { summary, refresh: refreshSummary } = usePlanQueueSummary(taskId);
  const [startedId, setStartedId] = useState('');
  const [discarded, setDiscarded] = useState('');
  const fromSummary = summary && summary.plan_id !== discarded ? summary.plan_id : '';
  // No plan here: the tracker may hold one (another kato split it) — followed.
  const shared = usePlanQueueShared(taskId, !startedId && !fromSummary);
  const fromTracker = shared && shared.plan_id !== discarded ? shared.plan_id : '';
  const planId = startedId || fromSummary || fromTracker;
  const { plan, setPlan, error } = usePlanQueueDetail(planId);
  // A split stopped before it had any steps is nothing to show: the tab
  // offers a new split (the folders, plan.md) instead of five empty columns.
  const nothingSplit = isEmptyStop(plan);
  const failed = plan?.status === 'propose_failed';
  const folders = usePlanQueueSplitFolders(taskId, !planId || nothingSplit || failed);
  const showLoader = useDelayedFlag(!!planId && !plan && !error, 250);

  const onPlan = useCallback((next) => {
    setPlan(next);
    refreshSummary();
    planQueueChanges.notify(taskId);
  }, [setPlan, refreshSummary, taskId]);

  const onDiscarded = useCallback(() => {
    setDiscarded(planId);
    setStartedId('');
    refreshSummary();
    planQueueChanges.notify(taskId);
  }, [planId, refreshSummary, taskId]);

  const [busy, run] = useBusyAction(async (makeCall, failureTitle, then) => {
    const body = await planAction(makeCall, failureTitle);
    if (body) { then(body); }
  });

  const started = (body) => {
    setDiscarded('');
    setStartedId(body.plan_id);
    onPlan(body);
  };
  const propose = (splitFolder = '') => run(
    () => proposePlanQueue(taskId, { splitFolder }), 'The plan was not split', started,
  );
  // From a failed split: throw it away (it created nothing) and start again.
  const startOver = (splitFolder = '') => run(async () => {
    const dropped = await discardPlanQueue(plan.plan_id);
    return dropped?.ok ? proposePlanQueue(taskId, { splitFolder }) : dropped;
  }, 'The plan was not split', started);

  let content;
  if (!planId || nothingSplit) {
    content = <SplitStart folders={folders} busy={busy} onPropose={propose} />;
  } else if (!plan) {
    content = error
      ? <p className="plan-queue-error" role="alert">Could not read the plan: {error}</p>
      : (showLoader ? <p className="plan-queue-note" role="status">Reading the plan…</p> : null);
  } else if (plan.status === 'proposing') {
    content = (
      <div className="plan-queue-start">
        <p role="status">{PLAN_STATUS_TEXT.proposing}</p>
        <p className="plan-queue-note">{IN_CHAT_NOTE}</p>
        <button type="button" className="plan-action is-danger" disabled={busy}
          data-tooltip="Stop waiting for this split. The agent's chat is not stopped — use the chat's Stop for that."
          onClick={() => run(() => controlPlanQueue(plan.plan_id, 'stop'), 'The proposal was not cancelled', onPlan)}>
          Cancel the split
        </button>
      </div>
    );
  } else if (plan.status === 'propose_failed') {
    content = (
      <div className="plan-queue-start">
        <p className="plan-queue-error" role="alert">{plan.reason || PLAN_STATUS_TEXT.propose_failed}</p>
        <button type="button" className="plan-action is-primary" disabled={busy}
          data-tooltip="Ask the agent in the chat again, the same way."
          onClick={() => run(() => retryPlanQueueProposal(plan.plan_id), 'The proposal was not retried', onPlan)}>
          Try again
        </button>
        <button type="button" className="plan-action is-danger" disabled={busy}
          data-tooltip="Throw this split away. Nothing was created."
          onClick={() => run(() => discardPlanQueue(plan.plan_id), 'The split was not discarded', onDiscarded)}>
          Discard
        </button>
        <SplitStart folders={folders} busy={busy} onPropose={startOver} intro="Or start over:" />
      </div>
    );
  } else if (plan.status === 'proposed') {
    content = (
      <PlanProposal key={`${plan.plan_id}:${plan.revision}`} plan={plan} onPlan={onPlan} onDiscarded={onDiscarded} />
    );
  } else {
    content = (
      <>
        <PlanBoard plan={plan} onPlan={onPlan} onOpenTask={onOpenTask} />
        {plan.status === 'cancelled' || plan.status === 'finished' ? (
          <div className="plan-queue-again">
            <button type="button" className="plan-action" disabled={busy}
              data-tooltip="Start a new split of this task's plan (this board stays as it was)."
              onClick={() => { setDiscarded(planId); setStartedId(''); }}>
              Split the plan again
            </button>
          </div>
        ) : null}
      </>
    );
  }

  const title = plan && !nothingSplit && isBoardStatus(plan.status) ? 'Plan board' : 'Split plan';
  return (
    <section id="plan-queue-pane" aria-label={`${title} — ${taskId}`}>
      <header className="plan-queue-pane-header">
        <span className="plan-queue-pane-title">{title}</span>
        <span className="plan-queue-pane-task">{taskId}</span>
      </header>
      <div className="plan-queue-pane-body">
        <div className="plan-queue-body">{content}</div>
      </div>
    </section>
  );
}

function isEmptyStop(plan) {
  return !!plan && plan.status === 'cancelled' && !(plan.steps || []).length;
}

// Before any split: take a split already written as files (one task per file,
// each file's text kept exactly as written), or have an agent split plan.md.
function SplitStart({ folders, busy, onPropose, intro = '' }) {
  return (
    <div className="plan-queue-start">
      <p>
        {intro || 'Split this task\'s plan into small tasks that wait for each other. You review the '
          + 'tasks and approve them like a plan before anything starts.'}
      </p>
      {folders.map((folder) => (
        <div key={folder.path} className="plan-queue-folder">
          <button type="button" className="plan-action is-primary" disabled={busy}
            data-tooltip={`The agent maps your ${folder.files} files in the chat: one task each, kept exactly as written.`}
            onClick={() => onPropose(folder.path)}>
            Use the split in {folder.path}/
          </button>
          <span className="plan-queue-note">
            {folder.files} files, one task each, kept exactly as written
            {folder.context ? ` (${folder.context} is read for the order)` : ''}.
            An agent only works out which task waits for which, and the repositories.
          </span>
        </div>
      ))}
      <button type="button" className={cx('plan-action', !folders.length && 'is-primary')}
        data-tooltip="The agent reads plan.md in the chat and proposes the tasks and their order."
        disabled={busy} onClick={() => onPropose('')}>
        Propose a split of plan.md
      </button>
    </div>
  );
}
