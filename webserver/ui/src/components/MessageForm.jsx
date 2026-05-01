export default function MessageForm({
  value,
  onChange,
  turnInFlight,
  onSubmit,
  disabled = false,
  disabledReason = '',
}) {
  function submit(event) {
    event.preventDefault();
    if (disabled) { return; }
    const trimmed = (value || '').trim();
    if (!trimmed) { return; }
    onSubmit(trimmed);
    onChange('');
  }

  const placeholder = disabled
    ? disabledReason || 'Session is not live — chat resumes when kato re-spawns it.'
    : 'Reply to Claude (Shift+Enter for newline)';
  const isSteering = turnInFlight && !disabled;
  const submitClass = isSteering ? 'is-steering' : '';
  const submitLabel = isSteering ? 'Steer' : 'Send';
  let submitTitle;
  if (disabled) {
    submitTitle = disabledReason || 'Session is not live';
  } else if (turnInFlight) {
    submitTitle = 'Claude is working — your message will steer the in-flight turn.';
  } else {
    submitTitle = '';
  }

  function handleChange(event) {
    onChange(event.target.value);
  }
  function handleKeyDown(event) {
    if (event.key === 'Enter' && !event.shiftKey) {
      submit(event);
    }
  }
  return (
    <form id="message-form" onSubmit={submit}>
      <textarea
        id="message-input"
        placeholder={placeholder}
        rows={2}
        value={value || ''}
        disabled={disabled}
        onChange={handleChange}
        onKeyDown={handleKeyDown}
      />
      <button
        type="submit"
        disabled={disabled}
        className={submitClass}
        title={submitTitle}
      >
        {submitLabel}
      </button>
    </form>
  );
}
