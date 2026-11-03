import { editPlanQueueSteps } from '../../api.js';
import { useBusyAction } from '../../hooks/useBusyAction.js';
import Icon from '../Icon.jsx';
import { planAction } from './planQueueActions.js';
import { canChangeStep, newStepId, stepRounds } from './planQueueHelpers.js';
import { StepList, asDraft, useStepDrafts } from './PlanStepRows.jsx';

// Changing an approved plan, from its board. The tasks that have not started
// can change — their text, what they wait for, where they work — and a task
// can be added or taken out. A task that started is locked: its agent already
// has its brief. On a shared tracker kato rewrites the waiting tasks' tickets.
// Why a locked task is locked, in a word.
const LOCKED_AS = { satisfied: 'done', cancelled: 'cancelled', failed: 'stuck' };

export default function PlanLiveEditor({ plan, onPlan, onClose }) {
  const locked = new Map(plan.steps
    .filter((step) => !canChangeStep(step))
    .map((step) => [step.id, `${LOCKED_AS[step.status] || 'started'} — ${step.task_id || step.id} stays as it is`]));
  const drafts = useStepDrafts(() => plan.steps.map(asDraft));
  const { steps, setSteps, setOpen } = drafts;
  const rounds = stepRounds(steps);
  const [busy, run] = useBusyAction(async (work) => work());

  function add() {
    const id = newStepId(steps);
    setSteps((current) => [
      ...current,
      { id, title: '', brief: '', depends_on: [], repositories: [...plan.repository_ids] },
    ]);
    setOpen((current) => new Set(current).add(id));
  }

  const save = () => run(async () => {
    const body = await planAction(() => editPlanQueueSteps(plan.plan_id, steps), 'The plan was not changed');
    if (body?.plan_id) {
      onPlan(body);
      onClose();
    }
  });

  return (
    <div className="plan-proposal plan-live-editor">
      <p className="plan-proposal-summary">
        Change the tasks that have not started. Started ones stay as they are.
        {plan.mode === 'tracker' ? ' Their tickets are rewritten to match when you save.' : ''}
      </p>
      <StepList drafts={drafts} rounds={rounds} repositories={plan.repository_ids}
        lockedText={(step) => locked.get(step.id) || ''} />
      <div className="plan-proposal-actions">
        <button type="button" className="plan-action plan-proposal-discard" disabled={busy} onClick={add}
          data-tooltip="A new task, waiting for nothing until you say what it waits for.">
          <Icon name="plus" /> Add a task to the plan
        </button>
        <button type="button" className="plan-action" disabled={busy} onClick={onClose}>
          Cancel
        </button>
        <button type="button" className="plan-action is-primary" disabled={busy} onClick={save}
          data-tooltip="Save the changed tasks. Started tasks are not touched.">
          Save changes
        </button>
      </div>
    </div>
  );
}
