// The model and effort the task's agent starts with. Each select shows the
// CONCRETE value that will run (never "Auto" / "Default"): the operator's
// pick, else the configured default — resolved by the caller.
export default function NewTaskAgentPickers({ models, levels, model, effort, onChange }) {
  const modelOptions = models.map((option) => (
    <option key={option.id} value={option.id}>{option.label || option.id}</option>
  ));
  const effortOptions = levels.map((level) => (
    <option key={level} value={level}>{level}</option>
  ));
  return (
    <section className="new-task-section new-task-agent">
      <label className="new-task-field">
        <span className="new-task-field-label">Model</span>
        <select value={model} onChange={(event) => onChange({ model: event.target.value })}>
          {modelOptions}
        </select>
      </label>
      <label className="new-task-field">
        <span className="new-task-field-label">Effort</span>
        <select value={effort} onChange={(event) => onChange({ effort: event.target.value })}>
          {effortOptions}
        </select>
      </label>
    </section>
  );
}
