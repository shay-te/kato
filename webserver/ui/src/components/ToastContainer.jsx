import { useEffect, useState } from 'react';
import { toastStore } from '../stores/toastStore.js';

// Renders the active toasts as a stack at top-center of the page.
// Mount this once at App level; it subscribes to the toast store on
// mount and re-renders when toasts are pushed or dismissed.
export default function ToastContainer() {
  const [toasts, setToasts] = useState([]);
  useEffect(() => toastStore.subscribe(setToasts), []);
  if (toasts.length === 0) { return null; }
  return (
    <div className="toast-container" role="status" aria-live="polite">
      {toasts.map((entry) => (
        <ToastCard
          key={entry.id}
          entry={entry}
          onDismiss={() => toastStore.dismiss(entry.id)}
        />
      ))}
    </div>
  );
}

function ToastCard({ entry, onDismiss }) {
  const className = `toast toast-${entry.kind || 'info'}`;
  // Task chip ABOVE the title for global popups that originate from a
  // task-specific action — the operator can tell at a glance which task
  // the toast belongs to even when the popup floats over a different
  // tab or the orchestrator view. Falls back to just the id when the
  // caller didn't pass a summary.
  const taskId = String(entry.taskId || '').trim();
  const taskSummary = String(entry.taskSummary || '').trim();
  return (
    <div
      className={className}
      onClick={onDismiss}
      role="alert"
      tabIndex={0}
      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === 'Escape') { onDismiss(); } }}
    >
      <span className="toast-glyph" aria-hidden="true">{_glyph(entry.kind)}</span>
      <div className="toast-body">
        {taskId && (
          <div className="toast-task" title={taskSummary ? `${taskId} — ${taskSummary}` : taskId}>
            <span className="toast-task-id">{taskId}</span>
            {taskSummary && (
              <span className="toast-task-summary">{taskSummary}</span>
            )}
          </div>
        )}
        {entry.title && <strong className="toast-title">{entry.title}</strong>}
        {entry.message && <pre className="toast-message">{entry.message}</pre>}
      </div>
      <button
        type="button"
        className="toast-close"
        aria-label="Dismiss notification"
        onClick={(e) => { e.stopPropagation(); onDismiss(); }}
      >
        ×
      </button>
    </div>
  );
}

function _glyph(kind) {
  switch (kind) {
    case 'success': return '✓';
    case 'error':   return '✗';
    case 'warning': return '⚠';
    default:        return 'ℹ';
  }
}
