import { REVIEW_LOOP_STAGE_LABELS } from './reviewLoopHelpers.js';
import {
  REVIEW_LOOP_STAGES,
  useReviewLoopStages,
  writeReviewLoopStage,
} from './reviewLoopStagesPref.js';

// The loop's optional stages, ticked before Start / Run again and remembered
// for every later loop (reviewLoopStagesPref) — read at the moment Start is
// clicked, like the round limit beside it.
export default function ReviewLoopStagesPicker({ disabled = false }) {
  const stages = useReviewLoopStages();
  const boxes = REVIEW_LOOP_STAGES.map((name) => (
    <label key={name} className="review-loop-stage">
      <input
        type="checkbox"
        checked={stages[name]}
        disabled={disabled}
        onChange={(event) => writeReviewLoopStage(name, event.target.checked)}
      />
      <span>{REVIEW_LOOP_STAGE_LABELS[name]}</span>
    </label>
  ));
  return (
    <fieldset className="review-loop-stages">
      <legend className="review-loop-stages-legend">Stages</legend>
      {boxes}
    </fieldset>
  );
}
