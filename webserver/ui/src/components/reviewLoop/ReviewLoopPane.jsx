import { useEffect } from 'react';
import { cx } from '../../utils/cx.js';
import { useNowSeconds } from '../../hooks/useNowSeconds.js';
import { useReviewLoop } from '../../hooks/useReviewLoop.js';
import Icon, { BusyIcon } from '../Icon.jsx';
import ReviewLoopRoundList from './ReviewLoopRoundList.jsx';
import ReviewLoopRoundsPicker from './ReviewLoopRoundsPicker.jsx';
import ReviewLoopTracker from './ReviewLoopTracker.jsx';
import {
  REVIEW_LOOP_EMPTY_TEXT,
  reviewLoopOutcome,
  reviewLoopSentence,
} from './reviewLoopHelpers.js';
import { markReviewLoopSeen } from './reviewLoopViewStore.js';

// The review loop view, in the centre pane (like the Plan view): where the
// loop is right now, then every round — what the reviewer found, what was sent
// to the chat, and the round's saved texts. Loops start HERE, after the
// operator has picked how many rounds (the header button only opens this).
export default function ReviewLoopPane({ session, onClose }) {
  const { summary, detail, running, start, starting, stop, stopping } = useReviewLoop(
    session, { withDetail: true },
  );
  const now = useNowSeconds(running);
  const loopId = summary?.loop_id || '';
  // A FINISHED loop seen here no longer needs the header to announce its
  // outcome. A running one is not marked: its outcome is still to come.
  useEffect(() => {
    if (loopId && !running) { markReviewLoopSeen(loopId); }
  }, [loopId, running]);
  const outcome = reviewLoopOutcome(summary);
  const status = paneStatus(summary, outcome, now);
  const action = running ? (
    <button type="button" className="review-loop-pane-action is-stop" onClick={stop} disabled={stopping}>
      <BusyIcon busy={stopping} idle="stop" />
      <span>Stop</span>
    </button>
  ) : (
    <>
      <ReviewLoopRoundsPicker disabled={starting} />
      <button type="button" className="review-loop-pane-action" onClick={start} disabled={starting}>
        <BusyIcon busy={starting} idle="play" />
        <span>{summary ? 'Run again' : 'Start'}</span>
      </button>
    </>
  );
  const body = summary ? (
    <>
      <ReviewLoopTracker loop={summary} now={now} />
      <ReviewLoopRoundList taskId={session?.task_id || ''} loop={detail} />
    </>
  ) : (
    <p className="review-loop-pane-empty">{REVIEW_LOOP_EMPTY_TEXT}</p>
  );
  return (
    <section id="review-loop-pane">
      <header className="review-loop-pane-header">
        <span className="review-loop-pane-title">Review loop</span>
        <span className={cx('review-loop-pane-status', `is-${status.tone}`)}>{status.text}</span>
        <div className="review-loop-pane-actions">{action}</div>
        <button
          type="button"
          className="review-loop-pane-close tooltip-end"
          onClick={onClose}
          data-tooltip="Close the review loop view. The loop keeps running."
          aria-label="Close the review loop view"
        >
          <Icon name="xmark" />
        </button>
      </header>
      <div className="review-loop-pane-body">{body}</div>
    </section>
  );
}

function paneStatus(summary, outcome, now) {
  if (!summary) { return { text: 'not run yet', tone: 'neutral' }; }
  if (!outcome) { return { text: reviewLoopSentence(summary, now), tone: 'running' }; }
  return { text: reviewLoopSentence(summary, now), tone: outcome.tone };
}
