import Tab from './Tab.jsx';

export default function TabList({
  sessions,
  activeTaskId,
  attentionTaskIds,
  onSelect,
  onForget,
}) {
  const isEmpty = !sessions || sessions.length === 0;
  if (isEmpty) {
    return (
      <aside id="tabs-pane">
        <p id="empty-state" className="empty">
          No active planning sessions yet. Tag a task with{' '}
          <code>kato:wait-planning</code> and run kato — a tab will appear here.
        </p>
      </aside>
    );
  }
  const tabs = sessions.map((session) => {
    const isActive = session.task_id === activeTaskId;
    const needsAttention = !!attentionTaskIds && attentionTaskIds.has(session.task_id);
    return (
      <Tab
        key={session.task_id}
        session={session}
        active={isActive}
        needsAttention={needsAttention}
        onSelect={onSelect}
        onForget={onForget}
      />
    );
  });
  return (
    <aside id="tabs-pane">
      <ul id="tab-list">
        {tabs}
      </ul>
    </aside>
  );
}
