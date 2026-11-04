import { useCallback, useEffect, useState } from 'react';
import { fetchTaskContainers, taskContainersAction } from '../api.js';
import { useAnchoredMenu } from '../hooks/useAnchoredMenu.js';
import { useBusyAction } from '../hooks/useBusyAction.js';
import { toastResult } from '../stores/toastStore.js';
import { cx } from '../utils/cx.js';
import AnchoredMenu from './AnchoredMenu.jsx';
import Icon from './Icon.jsx';
import { containersChipText, containersOutcomeToast, removeContainersConfirm } from './taskContainersText.js';

// The Docker containers the task's agent started in its folder (a Compose
// stack, a dev server), on the task's header — shown only when there are any,
// in the title area so the action buttons never move. A click lists them, with
// Stop all and Remove all. Done stops them by itself; deleting the task
// removes them. Never polled: read when the task opens, when its agent's
// status changes (``refreshKey``), when the list opens, and after an action.
export default function TaskContainersChip({ taskId, refreshKey = '' }) {
  const [listed, setListed] = useState(null);
  const menu = useAnchoredMenu();

  const load = useCallback(async () => {
    if (!taskId) { setListed(null); return; }
    try {
      const body = await fetchTaskContainers(taskId);
      setListed(body?.available ? body.containers || [] : []);
    } catch {
      setListed([]);  // kato or Docker unreachable: nothing to show
    }
  }, [taskId]);

  useEffect(() => { load(); }, [load, refreshKey]);

  const [busy, act] = useBusyAction(async (action) => {
    const result = await taskContainersAction(taskId, action);
    toastResult(containersOutcomeToast(action, result, taskId));
    await load();
  });

  const containers = listed || [];
  if (!containers.length) { return null; }
  const running = containers.filter((container) => container.running).length;

  function open(event) {
    if (!menu.open) { load(); }
    menu.toggle(event);
  }

  return (
    <>
      <button type="button" className={cx('task-containers-chip', running && 'is-running')}
        aria-haspopup="menu" aria-expanded={menu.open} onClick={open}
        data-tooltip="Docker containers this task's agent started in its folder. Click to stop or remove them.">
        <Icon name="box" />
        {containersChipText(containers)}
      </button>
      <AnchoredMenu menu={menu} label={`Containers of ${taskId}`} className="task-containers-menu">
        {containers.map((container) => (
          <div key={container.id} className="anchored-menu-item task-containers-row" role="menuitem">
            <span className={cx('task-containers-state', container.running && 'is-running')}
              aria-label={container.state} />
            <strong className="anchored-menu-id">{container.name}</strong>
            <span className="anchored-menu-name">{container.image} · {container.state}</span>
          </div>
        ))}
        <div className="task-containers-actions">
          <button type="button" className="plan-action" disabled={busy || !running}
            onClick={() => act('stop')}
            data-tooltip="Stop the running ones. Nothing is deleted; docker start brings one back.">
            Stop all
          </button>
          <button type="button" className="plan-action is-danger" disabled={busy}
            onClick={() => window.confirm(removeContainersConfirm(taskId, containers.length)) && act('remove')}
            data-tooltip="Remove them, with what Docker Compose made for them.">
            Remove all
          </button>
        </div>
      </AnchoredMenu>
    </>
  );
}
