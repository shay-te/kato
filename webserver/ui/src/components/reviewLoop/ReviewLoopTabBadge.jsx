import { cx } from '../../utils/cx.js';
import Icon from '../Icon.jsx';
import { reviewLoopChip, reviewLoopSentence } from './reviewLoopHelpers.js';

// A task's review loop on its row in the tasks menu: done or not, at a glance.
// Running (the glyph turns) = not done yet; clean (green) = done; any other end
// takes its outcome's colour (stuck, round limit, failed, stopped). Not a
// button: the toolbar's review loop button opens the loop. A finished loop
// keeps its mark — "was it done?" is the question the row answers.
export default function ReviewLoopTabBadge({ loop = null }) {
  const chip = reviewLoopChip(loop, 0);
  if (!chip) { return null; }
  return (
    <span
      className={cx('tab-review-loop-badge', `is-${chip.tone}`)}
      role="img"
      aria-label={`Review loop: ${chip.running ? 'running' : chip.text}`}
      title={`Review loop — ${reviewLoopSentence(loop, Date.now() / 1000)}`}
    >
      <Icon name="loop" spin={chip.running} />
    </span>
  );
}
