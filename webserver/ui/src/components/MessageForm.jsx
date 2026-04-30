export default function MessageForm({
  value,
  onChange,
  turnInFlight,
  onSubmit,
}) {
  function submit(event) {
    event.preventDefault();
    const trimmed = (value || '').trim();
    if (!trimmed) { return; }
    onSubmit(trimmed);
    onChange('');
  }

  return (
    <form id="message-form" onSubmit={submit}>
      <textarea
        id="message-input"
        placeholder="Reply to Claude (Shift+Enter for newline)"
        rows={2}
        value={value || ''}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey) {
            submit(e);
          }
        }}
      />
      <button
        type="submit"
        className={turnInFlight ? 'is-steering' : ''}
        title={turnInFlight
          ? 'Claude is working — your message will steer the in-flight turn.'
          : ''}
      >
        {turnInFlight ? 'Steer' : 'Send'}
      </button>
    </form>
  );
}
