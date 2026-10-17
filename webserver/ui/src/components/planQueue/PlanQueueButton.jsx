import { usePlanQueueSummary } from '../../hooks/usePlanQueue.js';
import Icon from '../Icon.jsx';
import { buttonDot, buttonLabel, planStatusLine } from './planQueueHelpers.js';
import { planQueueView } from './planQueueViewStore.js';

const IDLE_TOOLTIP = 'Split this task\'s plan into small tasks that run as a queue';

// The task toolbar's plan action: "Split plan" until the task has a plan
// queue, "Plan board" after; it opens the task's plan tab in the centre strip.
// A dot shows while an agent proposes or the plan runs, an amber one while a
// split waits for the operator's review. A fixed-size icon button like its neighbours —
// the label lives in the tooltip and the accessible name, so the bar never
// re-flows when a split starts or ends.
//
// ``disabled`` renders the same button inert for the empty header shown before
// a task is picked, so the bar does not jump when one is.
export default function PlanQueueButton({ session = null, disabled = false }) {
  const taskId = disabled ? '' : (session?.task_id || '');
  const { summary } = usePlanQueueSummary(taskId);
  const label = buttonLabel(summary);
  const dot = buttonDot(summary);
  const tooltip = summary ? `${label} — ${planStatusLine(summary)}` : IDLE_TOOLTIP;
  return (
    <button
      type="button"
      className="session-action plan-queue-button"
      data-plan-queue={summary ? summary.status : 'none'}
      data-tooltip={tooltip}
      aria-label={label}
      onClick={() => planQueueView.open(taskId, label)}
      disabled={disabled}
      tabIndex={disabled ? -1 : undefined}
    >
      <Icon name="board" />
      {dot ? <span className={`plan-queue-dot is-${dot}`} aria-hidden="true" /> : null}
    </button>
  );
}
