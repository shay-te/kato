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

  return (
    <form id="message-form" onSubmit={submit}>
      <textarea
        id="message-input"
        placeholder={placeholder}
        rows={2}
        value={value || ''}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey) {
            submit(e);
          }
        }}
      />
      <button
        type="submit"
        disabled={disabled}
        className={turnInFlight && !disabled ? 'is-steering' : ''}
        title={disabled
          ? (disabledReason || 'Session is not live')
          : turnInFlight
            ? 'Claude is working — your message will steer the in-flight turn.'
            : ''}
      >
        {turnInFlight && !disabled ? 'Steer' : 'Send'}
      </button>
    </form>
  );
}
