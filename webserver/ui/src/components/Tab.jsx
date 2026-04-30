export default function Tab({ session, active, needsAttention, onSelect }) {
  const baseStatus = session.status || 'active';
  const status = needsAttention ? 'attention' : baseStatus;
  const className = [
    'tab',
    active ? 'active' : '',
    needsAttention ? 'needs-attention' : '',
  ].filter(Boolean).join(' ');
  return (
    <li
      className={className}
      data-task-id={session.task_id}
      onClick={() => onSelect(session.task_id)}
    >
      <span
        className={`status-dot status-${status}`}
        title={needsAttention ? `${baseStatus} — needs your input` : baseStatus}
      />
      <strong>{session.task_id}</strong>
      <p>{session.task_summary || ''}</p>
    </li>
  );
}
