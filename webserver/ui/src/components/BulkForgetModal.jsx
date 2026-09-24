import { useEffect, useMemo, useState } from 'react';
import Icon, { BusyIcon } from './Icon.jsx';
import DialogShell from './DialogShell.jsx';
import { useEscapeKey } from '../hooks/useEscapeKey.js';
import { DELETE_STATE, deleteQueue } from '../stores/deleteQueueStore.js';

// Pick several tasks and delete them in one go, with per-task progress.
//
// Two operator reports drive this. "I have 10-15 tasks open which I keep in
// case something breaks" — so deleting is a periodic tidy-up of many tasks at
// once, not a one-off. And "I don't know you are working, so I delete the
// tasks multiple times" — so the run has to say, per task, which are waiting,
// which is going, and which are finished.
//
// The list is deliberately NOT filtered to "safe" tasks. The operator manages
// their own board; hiding rows would just send them back to deleting one at a
// time from the tab strip.

function StateCell({ entry }) {
  if (!entry) { return <span className="bulk-forget-state" />; }
  if (entry.state === DELETE_STATE.DELETING) {
    return (
      <span className="bulk-forget-state is-deleting">
        <BusyIcon busy idle="" /> deleting…
      </span>
    );
  }
  if (entry.state === DELETE_STATE.QUEUED) {
    return <span className="bulk-forget-state is-queued">queued</span>;
  }
  if (entry.state === DELETE_STATE.DONE) {
    return (
      <span className="bulk-forget-state is-done"><Icon name="check" /> deleted</span>
    );
  }
  return (
    <span className="bulk-forget-state is-failed" data-tooltip={entry.error}>
      <Icon name="warning" /> failed
    </span>
  );
}

export default function BulkForgetModal({ sessions = [], onClose, onRun }) {
  const [selected, setSelected] = useState(() => new Set());
  const [progress, setProgress] = useState(() => deleteQueue.snapshot());
  useEffect(() => deleteQueue.subscribe(setProgress), []);

  const running = useMemo(
    () => Object.values(progress).some(
      (e) => e.state === DELETE_STATE.QUEUED || e.state === DELETE_STATE.DELETING,
    ),
    [progress],
  );

  // Rows still on the board. A task that finished deleting drops out of
  // ``sessions`` on the next poll, so the run naturally shrinks the list.
  const rows = sessions;
  const allSelected = rows.length > 0 && rows.every((s) => selected.has(s.task_id));

  function toggle(taskId) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(taskId)) { next.delete(taskId); } else { next.add(taskId); }
      return next;
    });
  }

  function toggleAll() {
    setSelected(allSelected ? new Set() : new Set(rows.map((s) => s.task_id)));
  }

  const counts = useMemo(() => {
    const out = { done: 0, failed: 0 };
    for (const e of Object.values(progress)) {
      if (e.state === DELETE_STATE.DONE) { out.done += 1; }
      if (e.state === DELETE_STATE.FAILED) { out.failed += 1; }
    }
    return out;
  }, [progress]);

  // Esc closes, but never mid-run: the run is not cancellable server-side, so
  // letting the dialog vanish would hide progress the operator needs.
  useEscapeKey(running ? null : onClose);

  return (
    <DialogShell
      id="bulk-forget-modal"
      title="Delete tasks"
      subtitle="Removes each workspace clone and agent session. The ticket is not touched."
      ariaLabelledBy="bulk-forget-title"
      subtitleId="bulk-forget-subtitle"
      onClose={onClose}
      backdropClose={!running}
    >
      <div className="bulk-forget-body">
        <div className="bulk-forget-list" role="group" aria-label="Tasks">
          <label className="bulk-forget-row is-head">
            <input
              type="checkbox"
              checked={allSelected}
              onChange={toggleAll}
              disabled={running || rows.length === 0}
              aria-label="Select every task"
            />
            <span className="bulk-forget-id">Task</span>
            <span className="bulk-forget-state-head">Status</span>
          </label>
          {rows.map((session) => {
            const id = session.task_id;
            return (
              <label className="bulk-forget-row" key={id}>
                <input
                  type="checkbox"
                  checked={selected.has(id)}
                  onChange={() => toggle(id)}
                  disabled={running}
                  aria-label={`Select ${id}`}
                />
                <span className="bulk-forget-id">
                  {id}
                  {session.task_summary && (
                    <span className="bulk-forget-summary">{session.task_summary}</span>
                  )}
                </span>
                <StateCell entry={progress[id]} />
              </label>
            );
          })}
          {rows.length === 0 && (
            <p className="bulk-forget-empty">No tasks to delete.</p>
          )}
        </div>

        <div className="modal-actions bulk-forget-actions-row">
          {running ? (
            <span className="bulk-forget-progress" role="status">
              Deleting… {counts.done} done
              {counts.failed > 0 ? `, ${counts.failed} failed` : ''}
            </span>
          ) : (
            <span className="bulk-forget-progress">
              {counts.done > 0 || counts.failed > 0
                ? `${counts.done} deleted${counts.failed ? `, ${counts.failed} failed` : ''}`
                : `${selected.size} selected`}
            </span>
          )}
          <span className="bulk-forget-buttons">
            <button type="button" className="secondary" onClick={onClose} disabled={running}>
              {counts.done > 0 && !running ? 'Close' : 'Cancel'}
            </button>
            <button
              type="button"
              className="danger"
              disabled={running || selected.size === 0}
              onClick={() => onRun(Array.from(selected))}
            >
              {running ? 'Deleting…' : `Delete ${selected.size} task${selected.size === 1 ? '' : 's'}`}
            </button>
          </span>
        </div>
      </div>
    </DialogShell>
  );
}
