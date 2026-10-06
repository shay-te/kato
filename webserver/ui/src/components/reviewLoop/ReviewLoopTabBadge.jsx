import { cx } from '../../utils/cx.js';
import Icon from '../Icon.jsx';
import { reviewLoopChip } from './reviewLoopHelpers.js';
import { isReviewLoopSeen } from './reviewLoopViewStore.js';

// A small loop mark on a task's tab, so a running loop is visible from any
// task. Laid OVER the pill's corner (absolutely positioned) — a mark that took
// width would re-flow the whole tab strip every time a loop started or ended.
// The tab's hover card carries the words ("Review loop · round 2 of 5 · …").
export default function ReviewLoopTabBadge({ loop = null }) {
  const chip = reviewLoopChip(loop, 0);
  if (!chip || (!chip.running && isReviewLoopSeen(loop.loop_id))) { return null; }
  return (
    <span
      className={cx('tab-review-loop-badge', `is-${chip.tone}`)}
      aria-label={`Review loop: ${chip.text}`}
    >
      <Icon name="loop" />
    </span>
  );
}
