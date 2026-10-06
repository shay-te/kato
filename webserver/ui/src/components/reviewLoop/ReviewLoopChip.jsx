import { cx } from '../../utils/cx.js';
import { useNowSeconds } from '../../hooks/useNowSeconds.js';
import Icon from '../Icon.jsx';
import { reviewLoopChip, reviewLoopSentence } from './reviewLoopHelpers.js';
import { isReviewLoopSeen, reviewLoopView } from './reviewLoopViewStore.js';

// "Review loop · 2/5 · fixing · 4m 12s" beside the task title — where the loop
// is RIGHT NOW, at a glance. Click opens the view with every round.
//
// It sits in the title area, not among the header actions: the title already
// truncates, so the chip takes its room from there and the action buttons
// never move when a loop starts or ends.
//
// A finished loop's outcome ("clean", "stuck", …) stays until the operator
// has opened the view once; after that the chip goes away.
export default function ReviewLoopChip({ loop = null, taskId = '' }) {
  const now = useNowSeconds(loop?.status === 'running');
  const chip = reviewLoopChip(loop, now);
  if (!chip || (!chip.running && isReviewLoopSeen(loop.loop_id))) { return null; }
  return (
    <button
      type="button"
      className={cx('review-loop-chip', `is-${chip.tone}`)}
      data-tooltip={`${reviewLoopSentence(loop, now)}. Click to see every round.`}
      aria-label={`Review loop: ${chip.text}. Open the review loop.`}
      onClick={() => reviewLoopView.open(taskId)}
    >
      <Icon name="loop" />
      <span className="review-loop-chip-name">Review loop</span>
      <span className="review-loop-chip-text">{chip.text}</span>
    </button>
  );
}
