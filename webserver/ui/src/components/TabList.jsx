import Tab from './Tab.jsx';

export default function TabList({
  sessions,
  activeTaskId,
  attentionTaskIds,
  onSelect,
}) {
  if (!sessions || sessions.length === 0) {
    return (
      <aside id="tabs-pane">
        <p id="empty-state" className="empty">
          No active planning sessions yet. Tag a task with{' '}
          <code>kato:wait-planning</code> and run kato — a tab will appear here.
        </p>
      </aside>
    );
  }
  return (
    <aside id="tabs-pane">
      <ul id="tab-list">
        {sessions.map((session) => (
          <Tab
            key={session.task_id}
            session={session}
            active={session.task_id === activeTaskId}
            needsAttention={attentionTaskIds?.has(session.task_id) || false}
            onSelect={onSelect}
          />
        ))}
      </ul>
    </aside>
  );
}
