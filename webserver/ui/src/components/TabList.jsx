import Icon from './Icon.jsx';
import Tab from './Tab.jsx';

export default function TabList({
  sessions,
  activeTaskId,
  attentionTaskIds,
  onSelect,
  onForget,
  onOpenAddTask,
}) {
  const tabs = (sessions || []).map((session) => {
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
  // Header has a single ``+`` action that opens the task picker.
  // Always rendered (even when there are zero tabs) so the operator
  // has a way IN to kato on a fresh install — the empty-state copy
  // below also points at it.
  const header = (
    <header id="tabs-pane-header">
      <button
        type="button"
        id="tabs-add-task"
        data-tooltip="Add a task — pick from every task assigned to kato (open, in progress, in review, done) and provision its workspace."
        aria-label="Add a task"
        onClick={onOpenAddTask}
      >
        <Icon name="plus" />
        <span>Add task</span>
      </button>
    </header>
  );
  if (tabs.length === 0) {
    return (
      <aside id="tabs-pane">
        {header}
        <p id="empty-state" className="empty">
          No tabs yet. Click <strong>+ Add task</strong> above to pick
          one of your assigned tasks, or tag a YouTrack task with{' '}
          <code>kato:wait-planning</code> and let kato pick it up
          autonomously.
        </p>
      </aside>
    );
  }
  return (
    <aside id="tabs-pane">
      {header}
      <ul id="tab-list">
        {tabs}
      </ul>
    </aside>
  );
}
