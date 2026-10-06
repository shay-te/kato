import { cx } from '../../utils/cx.js';
import { reviewLoopSteps } from './reviewLoopHelpers.js';

// Where a running loop is right now, as steps of the current round:
//   ✓ Reviewed (3 blocking · 2 minor) → ✓ Sent to the chat →
//   ● Fixing in the main chat · 4m 12s → ○ Review round 3
// The current step names what it is waiting on and for how long.
export default function ReviewLoopTracker({ loop, now }) {
  const steps = reviewLoopSteps(loop, now);
  if (steps.length === 0) { return null; }
  const items = steps.map((step) => {
    const detail = step.detail ? <span className="review-loop-step-detail">{step.detail}</span> : null;
    return (
      <li key={step.key} className={cx('review-loop-step', `is-${step.state}`)} data-step={step.key}>
        <span className="review-loop-step-mark" aria-hidden="true" />
        <span className="review-loop-step-label">{step.label}</span>
        {detail}
      </li>
    );
  });
  return (
    <ol className="review-loop-tracker" aria-label={`Round ${loop.round} of ${loop.max_rounds}`}>
      {items}
    </ol>
  );
}
