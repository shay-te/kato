import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { useClampedPointMenu } from '../hooks/useClampedPointMenu.js';
import { deriveAgentStatus } from '../utils/agentStatus.js';
import { cx } from '../utils/cx.js';
import Icon from './Icon.jsx';

// The count on a main task's tab: its plan's sub-tasks, folded into it so a
// split does not fill the strip (utils/subtaskTabs.js). A click lists them,
// each with its own status dot, and opens the one picked. The count carries
// the attention mark when any of them is waiting for you.
//
// ``subtasks``: ``[{ session, liveStatus, needsAttention, name }]`` — the dot
// is ``deriveAgentStatus``'s own, as on every other surface.
export default function SubtaskTabMenu({ taskId, subtasks, onSelect }) {
  const [anchor, setAnchor] = useState(null);
  const { menuRef, style } = useClampedPointMenu(anchor);
  const open = anchor !== null;
  const close = () => setAnchor(null);

  useEffect(() => {
    if (!open) { return undefined; }
    const onKey = (event) => { if (event.key === 'Escape') { setAnchor(null); } };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open]);

  // No stopPropagation needed: the tab ignores clicks on its own buttons.
  function toggle(event) {
    if (open) { close(); return; }
    // Read the rect now: ``currentTarget`` is gone once the event ends.
    const rect = event.currentTarget.getBoundingClientRect();
    setAnchor({ x: rect.left, y: rect.bottom + 4 });
  }

  const count = subtasks.length;
  const asking = subtasks.some((entry) => entry.needsAttention);
  return (
    <>
      <button
        type="button"
        className={cx('tab-subtasks', asking && 'needs-attention', open && 'is-open')}
        aria-label={`${count} sub-task${count === 1 ? '' : 's'} of ${taskId}`}
        aria-haspopup="menu"
        aria-expanded={open}
        title={`${count} sub-task${count === 1 ? '' : 's'} of ${taskId}${asking ? ' — one is waiting for you' : ''}. Click to open one.`}
        onClick={toggle}
      >
        <Icon name="subtask" />
        {count}
      </button>
      {open ? createPortal(
        <>
          <div className="tab-subtasks-backdrop" onClick={close} aria-hidden="true" />
          <div ref={menuRef} className="tab-subtasks-menu" style={style} role="menu"
            aria-label={`Sub-tasks of ${taskId}`}>
            {subtasks.map(({ session, liveStatus, needsAttention, name }) => {
              const agent = deriveAgentStatus(session, liveStatus, needsAttention);
              return (
                <button key={session.task_id} type="button" role="menuitem" className="tab-subtasks-item"
                  title={agent.title}
                  onClick={() => { close(); onSelect(session.task_id); }}>
                  <span className={agent.dotClass} />
                  <strong className="tab-subtasks-id">{session.task_id}</strong>
                  <span className="tab-subtasks-name">{name}</span>
                </button>
              );
            })}
          </div>
        </>,
        document.body,
      ) : null}
    </>
  );
}
