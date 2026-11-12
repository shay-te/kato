import { cx } from '../utils/cx.js';
import { deriveAgentStatus } from '../utils/agentStatus.js';
import { parentOfTab } from '../utils/subtaskTabs.js';
import { readTabNames, tabNameFor } from '../utils/taskTabNames.js';
import { withoutSubtaskPrefix } from './planQueue/subtaskLabel.js';
import { useTaskSwitcherVisible } from '../stores/taskSwitchStore.js';

// The most rows shown at once. More tasks than this scroll the window so the
// task you landed on stays in it, with its neighbours either side.
const WINDOW = 9;

// The Cmd+Tab-style overlay: while the operator moves between tasks with Tab /
// Ctrl+Tab, the tasks show in the middle of the screen in the tasks menu's
// order, the one just landed on highlighted. It fades a moment after the last
// press (``taskSwitchStore``). Nothing to click: switching already happened on
// each press, as it always has; this only shows where you are.
//
// Each dot is ``deriveAgentStatus``'s own — the one derivation every status
// surface renders (the menu rows included); nothing here decides a status.
export default function TaskSwitcher({
  sessions = [], order = [], activeTaskId = '', agentStatuses = {}, attentionTaskIds = null,
}) {
  const visible = useTaskSwitcherVisible();
  if (!visible) { return null; }
  const byId = new Map((sessions || []).map((session) => [session.task_id, session]));
  const ids = (order && order.length ? order : [...byId.keys()]).filter((id) => byId.has(id));
  if (ids.length === 0) { return null; }
  const at = Math.max(0, ids.indexOf(activeTaskId));
  const start = Math.max(0, Math.min(at - Math.floor(WINDOW / 2), ids.length - WINDOW));
  const shown = ids.slice(start, start + WINDOW);
  const names = readTabNames();
  const listed = new Set(ids.map((id) => id.toLowerCase()));
  const active = byId.get(activeTaskId);
  return (
    <div className="task-switcher" role="status" aria-live="polite"
      aria-label={active ? `On ${active.task_id}` : 'Switching tasks'}>
      <ul className="task-switcher-list">
        {shown.map((id) => {
          const session = byId.get(id);
          const agent = deriveAgentStatus(
            session, agentStatuses[id] || null, !!attentionTaskIds && attentionTaskIds.has(id),
          );
          const parent = parentOfTab(session).toLowerCase();
          return (
            <li
              key={id}
              data-task-id={id}
              className={cx(
                'task-switcher-item',
                id === activeTaskId && 'is-active',
                parent && listed.has(parent) && 'is-nested',
              )}
            >
              <span className={agent.dotClass} />
              <strong className="task-switcher-id">{id}</strong>
              <span className="task-switcher-title">
                {withoutSubtaskPrefix(tabNameFor(names, id, session.task_summary))}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
