import { useState } from 'react';
import {
  addPlanDoneState, controlPlanQueue, fetchPlanQueue, forgetTaskWorkspace, planQueueStepAction,
  syncPlanQueue, takeOverPlanQueue,
} from '../../api.js';
import { toastResult } from '../../stores/toastStore.js';
import { useBusyAction } from '../../hooks/useBusyAction.js';
import { cx } from '../../utils/cx.js';
import Icon from '../Icon.jsx';
import {
  LIVE_PLAN,
  PLAN_STATUS_TEXT,
  boardColumns,
  canEditPlan,
  cardActions,
  headerActions,
  isFollowed,
  prChips,
  stepCode,
  stepNote,
  stepTaskState,
  unknownStateOf,
  stepWorker,
} from './planQueueHelpers.js';
import { planAction } from './planQueueActions.js';
import { deleteStepConfirm, deletedStepToast, movedToDoneToast } from './planStepDeleteText.js';
import PlanLiveEditor from './PlanLiveEditor.jsx';

const CONTROL_LABELS = { run: 'Run', pause: 'Pause', stop: 'Stop' };
const STOP_CONFIRM = 'Stop this plan? Tasks that have not started are cancelled; '
  + 'tasks already running carry on as ordinary tasks.';
const TAKE_OVER_CONFIRM = 'Take this plan over? From now on this kato releases its next sub-tasks '
  + '(to the user it works as), and the kato that drove it only shows it. It starts paused — press Run.';

// The board: where every task of the plan stands. Cards are never dragged —
// the tracker (or kato, for local tasks) moves them. Run / Pause / Stop act on
// the whole plan; a ready card can be started on its own, a waiting one counted
// as done, and a stuck one offers its way out.
//
// A card's code opens its task (``onOpenTask``) once it has one.
export default function PlanBoard({ plan, onPlan, onOpenTask = null }) {
  const [editing, setEditing] = useState(false);
  const [busy, act] = useBusyAction(async (makeCall, failureTitle) => {
    const body = await planAction(makeCall, failureTitle);
    if (body?.plan_id) { onPlan(body); }
  });

  function control(action) {
    if (action === 'stop' && !window.confirm(STOP_CONFIRM)) { return; }
    act(() => controlPlanQueue(plan.plan_id, action), `Could not ${action} the plan`);
  }

  // One click for every card stuck in that state: it is added to the states
  // that mean done, and the board is read again.
  function countStateDone(state) {
    if (!window.confirm(countStateDoneConfirm(state))) { return; }
    act(async () => {
      const added = await addPlanDoneState(state);
      if (!added.ok) { return added; }
      return { ok: true, body: await fetchPlanQueue(plan.plan_id) };
    }, `Could not count “${state}” as done`);
  }

  // A started card's Done: its ticket moves to Done, and nothing else — no
  // push, no pull request (often another kato's task, with no clone here).
  // The task's own Done, in its header, is still the one that publishes.
  function finishStep(step) {
    act(async () => {
      const result = await planQueueStepAction(plan.plan_id, step.id, 'done');
      if (result.ok) { toastResult(movedToDoneToast(stepCode(step))); }
      return result;
    }, `Could not move ${stepCode(step)} to Done`);
  }

  // Delete a sub-task: its pull requests declined and the step out of the
  // plan (the server), then the task itself — folder, chat, containers —
  // through the tab's own delete. Said in one toast, problems first.
  function deleteStep(step) {
    if (!window.confirm(deleteStepConfirm(step))) { return; }
    act(async () => {
      const result = await planQueueStepAction(plan.plan_id, step.id, 'delete');
      if (!result.ok) { return result; }
      const deleted = result.body?.deleted || {};
      const forgot = deleted.task_id ? await forgetTaskWorkspace(deleted.task_id) : null;
      toastResult(deletedStepToast(stepCode(step), deleted, forgot));
      return result;
    }, `Could not delete ${stepCode(step)}`);
  }

  function takeOver() {
    if (!window.confirm(TAKE_OVER_CONFIRM)) { return; }
    act(() => takeOverPlanQueue(plan.plan_id), 'Could not take the plan over');
  }

  // Read-only: reread the tracker now instead of waiting out the usual
  // cache/backoff. Offered on a followed board too, unlike Take over/Edit.
  function sync() {
    act(() => syncPlanQueue(plan.plan_id), 'Could not sync the plan');
  }

  // Another kato drives it: shown here, changed there — or after Take over.
  const followed = isFollowed(plan);

  return (
    <div className="plan-board">
      <div className="plan-board-head">
        <p className="plan-board-status">
          {PLAN_STATUS_TEXT[plan.status] || plan.status}
          {plan.reason ? <span className="plan-board-reason"> — {plan.reason}</span> : null}
          {plan.start_mode === 'plan' ? (
            <span className="plan-board-driver"> · each task plans first</span>
          ) : null}
          {plan.mode && plan.mode !== 'local' ? (
            <span className="plan-board-driver">
              {' '}· sub-tasks of {plan.source_task_id}, driven by {followed ? `${plan.driver_user}'s kato` : 'this kato'}
            </span>
          ) : null}
        </p>
        <div className="plan-board-controls">
          {canEditPlan(plan) && !editing ? (
            <button type="button" className="plan-board-control" disabled={busy} onClick={() => setEditing(true)}
              data-tooltip="Change the tasks that have not started: their text, what they wait for, where they work; add or take out a task.">
              Edit tasks
            </button>
          ) : null}
          {followed && !['finished', 'cancelled'].includes(plan.status) ? (
            <button type="button" className="plan-board-control" disabled={busy} onClick={takeOver}>
              Take over
            </button>
          ) : null}
          {LIVE_PLAN.includes(plan.status) ? (
            <button type="button" className="plan-board-control" disabled={busy} onClick={sync}
              data-tooltip="Reread every task's tracker status and pull-request state now, instead of waiting.">
              Sync
            </button>
          ) : null}
          {(followed ? [] : headerActions(plan.status)).map((action) => (
            <button key={action} type="button" disabled={busy}
              className={cx('plan-board-control', action === 'stop' && 'is-stop')}
              onClick={() => control(action)}>
              {CONTROL_LABELS[action]}
            </button>
          ))}
        </div>
      </div>
      {editing && canEditPlan(plan) ? (
        <PlanLiveEditor plan={plan} onPlan={onPlan} onClose={() => setEditing(false)} />
      ) : (
        <div className="plan-board-columns">
          {boardColumns(plan).map((column) => (
            <section key={column.id} className="plan-board-column" aria-label={column.label}>
              <h3 className="plan-board-column-title">
                {column.label}
                <span className="plan-board-count">{column.steps.length}</span>
              </h3>
              <ul className="plan-board-cards">
                {column.steps.map((step) => (
                  <PlanCard
                    key={step.id}
                    step={step}
                    planStatus={plan.status}
                    busy={busy}
                    readOnly={followed}
                    onDelete={followed || !canEditPlan(plan) ? null : () => deleteStep(step)}
                    unknownState={followed ? '' : unknownStateOf(step, plan)}
                    onCountStateDone={countStateDone}
                    onOpenTask={onOpenTask}
                    onAction={(entry) => {
                      if (entry.action === 'done') { finishStep(step); return; }
                      if (entry.confirm && !window.confirm(entry.confirm)) { return; }
                      act(
                        () => planQueueStepAction(plan.plan_id, step.id, entry.action),
                        `Could not ${entry.label.toLowerCase()} ${stepCode(step)}`,
                      );
                    }}
                  />
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}

function PlanCard({
  step, planStatus, busy, readOnly, onOpenTask, onAction, unknownState = '', onCountStateDone = () => {},
  onDelete = null,
}) {
  const note = stepNote(step, planStatus);
  const worker = stepWorker(step);
  const chips = prChips(step);
  const taskState = stepTaskState(step);
  const actions = readOnly ? [] : cardActions(step, planStatus);
  return (
    <li className={cx('plan-card', `is-${step.status}`)} data-step={step.id}>
      {step.task_id && onOpenTask ? (
        <button type="button" className="plan-card-code" onClick={() => onOpenTask(step.task_id)}
          data-tooltip={`Open ${step.task_id}`}>
          {step.task_id}
        </button>
      ) : <code className="plan-card-code">{stepCode(step)}</code>}
      {onDelete ? (
        <button type="button" className="plan-card-delete" disabled={busy} onClick={onDelete}
          aria-label={`Delete ${stepCode(step)}`}
          data-tooltip="Delete this task: decline its pull requests, take it out of the plan, delete its folder.">
          <Icon name="trash" />
        </button>
      ) : null}
      <span className="plan-card-title">{step.title}</span>
      {taskState ? (
        <span className="plan-card-state" data-tooltip="Its ticket's state on the tracker">{taskState}</span>
      ) : null}
      {worker ? <span className="plan-card-worker">{worker}</span> : null}
      {note ? <span className="plan-card-note">{note}</span> : null}
      {unknownState ? (
        <span className="plan-card-unknown-state">
          “{unknownState}” is not counted as done here, so this card will not move.
          <button type="button" className="plan-card-action" disabled={busy}
            data-tooltip="Add it to Settings → General → States that mean done. Every task in it then counts as done, and the tasks after it start once its pull request is merged."
            onClick={() => onCountStateDone(unknownState)}>
            Count “{unknownState}” as done
          </button>
        </span>
      ) : null}
      {chips.length ? (
        <span className="plan-card-prs">
          {chips.map((chip) => (chip.url ? (
            <a key={chip.repository} className={cx('plan-pr', `is-${chip.state}`)} href={chip.url}
              target="_blank" rel="noreferrer">
              {chip.repository}: {chip.state}
            </a>
          ) : (
            <span key={chip.repository} className={cx('plan-pr', `is-${chip.state}`)}>
              {chip.repository}: {chip.state}
            </span>
          )))}
        </span>
      ) : null}
      {actions.length ? (
        <span className="plan-card-actions">
          {actions.map((entry) => (
            <button key={entry.action} type="button" className="plan-card-action" disabled={busy}
              data-tooltip={entry.tip || undefined} onClick={() => onAction(entry)}>
              {entry.label}
            </button>
          ))}
        </span>
      ) : null}
    </li>
  );
}

function countStateDoneConfirm(state) {
  return `Count every ticket in “${state}” as done? It is added to Settings → General → States that `
    + 'mean done. A task there then waits only for its pull request to be merged, and the tasks after it '
    + 'start once it is.';
}
