import { useState } from 'react';
import { postSession } from '../api.js';
import { deriveTabStatus, tabStatusTitle } from '../utils/tabStatus.js';

export default function SessionHeader({ session, onStopped }) {
  const [stopping, setStopping] = useState(false);
  if (!session) { return null; }
  const status = deriveTabStatus(session);
  const isLoading = status === 'provisioning';

  async function onStop() {
    setStopping(true);
    const result = await postSession(session.task_id, 'stop');
    setStopping(false);
    if (typeof onStopped === 'function') {
      onStopped(result);
    }
  }

  return (
    <header id="session-header">
      <span
        id="session-status-dot"
        className={[
          'status-dot',
          `status-${status}`,
          isLoading ? 'is-loading' : '',
        ].filter(Boolean).join(' ')}
        title={tabStatusTitle(status)}
      />
      <strong id="session-task-id">{session.task_id}</strong>
      <span id="session-task-summary">{session.task_summary || ''}</span>
      <button
        id="session-stop"
        type="button"
        title="Stop the live Claude subprocess for this task"
        onClick={onStop}
        disabled={stopping || status !== 'active'}
      >
        {stopping ? 'Stopping…' : 'Stop'}
      </button>
    </header>
  );
}
