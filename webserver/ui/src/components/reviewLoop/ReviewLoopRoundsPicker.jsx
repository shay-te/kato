import {
  REVIEW_LOOP_ROUND_CHOICES,
  useReviewLoopRounds,
  writeReviewLoopRounds,
} from './reviewLoopRoundsPref.js';
import { reviewLoopRoundsText } from './reviewLoopHelpers.js';

// "Up to [5 rounds]" beside Start / Run again: how many reviews the next loop
// may run. The pick is remembered (reviewLoopRoundsPref) for every later loop,
// on any task, and read at the moment Start is clicked.
export default function ReviewLoopRoundsPicker({ disabled = false }) {
  const rounds = useReviewLoopRounds();
  return (
    <label
      className="review-loop-round-limit"
      data-tooltip="The most reviews this loop runs. It stops sooner when a review comes back clean."
    >
      <span>Up to</span>
      <select
        className="review-loop-round-limit-select"
        aria-label="Review loop rounds"
        value={rounds}
        onChange={(event) => writeReviewLoopRounds(event.target.value)}
        disabled={disabled}
      >
        {REVIEW_LOOP_ROUND_CHOICES.map((choice) => (
          <option key={choice} value={choice}>{reviewLoopRoundsText(choice)}</option>
        ))}
      </select>
    </label>
  );
}
