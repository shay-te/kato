import { START_MODE_OPTIONS } from './newTaskHelpers.js';

// How the task starts once its repositories are cloned. Plan is first and the
// default. Same radio cards as Chat settings.
export default function NewTaskStartMode({ value, onChange }) {
  const options = START_MODE_OPTIONS.map((option) => {
    const checked = value === option.value;
    return (
      <label key={option.value} className="chat-settings-option">
        <input
          type="radio"
          name="new-task-start-mode"
          value={option.value}
          checked={checked}
          onChange={() => onChange(option.value)}
        />
        <span className="chat-settings-option-text">
          <span className="chat-settings-option-label">{option.label}</span>
          <span className="chat-settings-option-hint">{option.hint}</span>
        </span>
      </label>
    );
  });
  return (
    <fieldset className="chat-settings-fieldset new-task-section">
      <legend className="chat-settings-legend">Start in</legend>
      {options}
    </fieldset>
  );
}
