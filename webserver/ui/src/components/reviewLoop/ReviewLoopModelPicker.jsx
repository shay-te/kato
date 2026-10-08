import { useReviewerModelChoice } from '../../hooks/useReviewerModelChoice.js';
import { writeReviewLoopModel } from './reviewLoopModelPref.js';

// "Reviewed by [Opus 5.5 (1M context) — kato's default]" beside the round
// limit: the model every review of the next loop runs on. Remembered for every
// later loop; the default entry stores "no pick", so it follows kato's setting.
export default function ReviewLoopModelPicker({ disabled = false }) {
  const choice = useReviewerModelChoice();
  const options = choice.options.map((option) => (
    <option key={option.id} value={option.id}>{option.label}</option>
  ));
  const pick = (event) => {
    const model = event.target.value;
    writeReviewLoopModel(model === choice.defaultId ? '' : model);
  };
  return (
    <label
      className="review-loop-round-limit"
      data-tooltip="The model the independent reviews run on. The fixes still run in the task's chat, on its own model."
    >
      <span>Reviewed by</span>
      <select
        className="review-loop-round-limit-select review-loop-model-select"
        aria-label="Review loop model"
        value={choice.value}
        onChange={pick}
        disabled={disabled || options.length === 0}
      >
        {options}
      </select>
    </label>
  );
}
