import { cx } from '../../utils/cx.js';
import { useReviewLoop } from '../../hooks/useReviewLoop.js';
import Icon from '../Icon.jsx';
import {
  REVIEW_LOOP_IDLE_TOOLTIP,
  reviewLoopChip,
  reviewLoopSentence,
} from './reviewLoopHelpers.js';
import { isReviewLoopSeen, reviewLoopView } from './reviewLoopViewStore.js';

// The header action: opens the review loop view. Nothing starts from here —
// the view is where the operator picks how many rounds, then Start / Run
// again (and Stop while one runs).
//
// The loop glyph, in its own group of the header (a separator follows it), so
// it never reads as one of the fast prompts beside it.
//
// A fixed-size icon button like its neighbours. Whether a loop is running —
// or finished and not yet looked at — is a small dot laid OVER the corner, so
// the bar never re-flows when a loop starts or ends; the readable position
// ("2/5 · fixing") is the chip next to the task title.
//
// ``disabled`` renders the same button inert for the empty header shown before
// a task is picked, so the bar does not jump when one is.
export default function ReviewLoopButton({ session = null, disabled = false }) {
  const { summary } = useReviewLoop(session, { announceFinish: true });
  const taskId = session?.task_id || '';
  const chip = reviewLoopChip(summary, Date.now() / 1000);
  const showDot = !!chip && (chip.running || !isReviewLoopSeen(summary?.loop_id));
  const tooltip = summary
    ? `Review loop — ${reviewLoopSentence(summary, Date.now() / 1000)}. Click to see every round.`
    : REVIEW_LOOP_IDLE_TOOLTIP;
  const dot = showDot ? <span className={cx('review-loop-dot', `is-${chip.tone}`)} aria-hidden="true" /> : null;
  return (
    <button
      type="button"
      className="session-action review-loop-button"
      data-review-loop={chip ? chip.tone : 'none'}
      data-tooltip={tooltip}
      onClick={() => reviewLoopView.open(taskId)}
      disabled={disabled}
      tabIndex={disabled ? -1 : undefined}
      aria-label="Review loop"
    >
      <Icon name="loop" />
      {dot}
    </button>
  );
}
