import { cx } from '../../utils/cx.js';
import Icon from '../Icon.jsx';
import { buttonDot, planRowLine } from './planQueueHelpers.js';

// The split plan on the row of the task it was split from: done or not, at a
// glance. A live plan (``plan``, the tab list's ``plan_queue``) is not done —
// amber while it runs or an agent proposes, orange while a split waits for
// review, grey otherwise; a finished one (``finished``) is green. A stopped
// plan shows nothing. Not a button: the toolbar's plan button opens the plan.
export default function PlanQueueTabBadge({ plan = null, finished = false }) {
  const words = planRowLine(plan, finished);
  if (!words) { return null; }
  const tone = plan ? (buttonDot(plan) || 'idle') : 'good';
  return (
    <span
      className={cx('tab-plan-queue-badge', `is-${tone}`)}
      role="img"
      aria-label={`Split plan: ${words}`}
      title={`Split plan — ${words}`}
    >
      <Icon name="board" />
    </span>
  );
}
