import { cx } from '../../utils/cx.js';
import Icon from '../Icon.jsx';
import { buttonDot, planStatusLine } from './planQueueHelpers.js';

// The split's mark on the task it was split from, so a plan in progress is
// visible from the tab strip. Laid over the pill's top-right corner (the
// review loop's mark takes the top-left) — a mark that took width would re-flow
// the strip every time a plan started or ended. ``plan`` is the tab list's
// ``plan_queue``: there only while the plan is neither finished nor stopped.
// The tab's hover card carries the words.
export default function PlanQueueTabBadge({ plan = null }) {
  if (!plan) { return null; }
  return (
    <span
      className={cx('tab-plan-queue-badge', `is-${buttonDot(plan) || 'idle'}`)}
      aria-label={`Split plan: ${planStatusLine(plan)}`}
    >
      <Icon name="board" />
    </span>
  );
}
