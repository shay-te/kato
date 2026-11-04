import { useAnchoredMenu } from '../hooks/useAnchoredMenu.js';
import { deriveAgentStatus } from '../utils/agentStatus.js';
import { cx } from '../utils/cx.js';
import AnchoredMenu from './AnchoredMenu.jsx';
import Icon from './Icon.jsx';

// The count on a main task's tab: its plan's sub-tasks, folded into it so a
// split does not fill the strip (utils/subtaskTabs.js). A click lists them,
// each with its own status dot, and opens the one picked. The count carries
// the attention mark when any of them is waiting for you.
//
// ``subtasks``: ``[{ session, liveStatus, needsAttention, name }]`` — the dot
// is ``deriveAgentStatus``'s own, as on every other surface. No
// stopPropagation on the count: the tab ignores clicks on its own buttons.
export default function SubtaskTabMenu({ taskId, subtasks, onSelect }) {
  const menu = useAnchoredMenu();
  const count = subtasks.length;
  const asking = subtasks.some((entry) => entry.needsAttention);
  return (
    <>
      <button
        type="button"
        className={cx('tab-subtasks', asking && 'needs-attention', menu.open && 'is-open')}
        aria-label={`${count} sub-task${count === 1 ? '' : 's'} of ${taskId}`}
        aria-haspopup="menu"
        aria-expanded={menu.open}
        title={`${count} sub-task${count === 1 ? '' : 's'} of ${taskId}${asking ? ' — one is waiting for you' : ''}. Click to open one.`}
        onClick={menu.toggle}
      >
        <Icon name="subtask" />
        {count}
      </button>
      <AnchoredMenu menu={menu} label={`Sub-tasks of ${taskId}`}>
        {subtasks.map(({ session, liveStatus, needsAttention, name }) => {
          const agent = deriveAgentStatus(session, liveStatus, needsAttention);
          return (
            <button key={session.task_id} type="button" role="menuitem" className="anchored-menu-item"
              title={agent.title}
              onClick={() => { menu.close(); onSelect(session.task_id); }}>
              <span className={agent.dotClass} />
              <strong className="anchored-menu-id">{session.task_id}</strong>
              <span className="anchored-menu-name">{name}</span>
            </button>
          );
        })}
      </AnchoredMenu>
    </>
  );
}
