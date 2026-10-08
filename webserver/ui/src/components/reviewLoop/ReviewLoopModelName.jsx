import { useReviewerModelChoice } from '../../hooks/useReviewerModelChoice.js';
import { reviewerModelName } from './reviewLoopHelpers.js';

// While a loop runs: which model its reviews run on (fixed when it started).
export default function ReviewLoopModelName({ model }) {
  const choice = useReviewerModelChoice();
  const name = reviewerModelName(model, choice);
  return (
    <span className="review-loop-model" title="The model this loop's reviews run on">
      {`Reviewed by ${name}`}
    </span>
  );
}
