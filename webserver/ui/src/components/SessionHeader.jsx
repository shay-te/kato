import { useState } from 'react';
import { postSession } from '../api.js';
import { TAB_STATUS } from '../constants/tabStatus.js';
import { usePushApproval } from '../hooks/usePushApproval.js';
import { deriveTabStatus, resolveTabStatus, tabStatusTitle } from '../utils/tabStatus.js';

export default function SessionHeader({ session, needsAttention = false, onStopped }) {
  const [stopping, setStopping] = useState(false);
  const pushApproval = usePushApproval(session?.task_id || '');
  if (!session) { return null; }
  const baseStatus = deriveTabStatus(session);
  const status = resolveTabStatus(session, needsAttention);
  const isLoading = baseStatus === TAB_STATUS.PROVISIONING;

  async function onStop() {
    setStopping(true);
    const result = await postSession(session.task_id, 'stop');
    setStopping(false);
    if (typeof onStopped === 'function') {
      onStopped(result);
    }
  }

  const dotClass = [
    'status-dot',
    `status-${status}`,
    isLoading ? 'is-loading' : '',
  ].filter(Boolean).join(' ');
  const stopLabel = stopping ? 'Stopping…' : 'Stop';
  const pushLabel = pushApproval.busy ? 'Pushing…' : 'Approve push';
  const approvePushButton = pushApproval.awaiting && (
    <button
      id="session-approve-push"
      type="button"
      title="Approve push: kato will push the branch and open the PR"
      onClick={pushApproval.approve}
      disabled={pushApproval.busy}
    >
      {pushLabel}
    </button>
  );
  return (
    <header id="session-header">
      <span
        id="session-status-dot"
        className={dotClass}
        title={tabStatusTitle(baseStatus, needsAttention)}
      />
      <strong id="session-task-id">{session.task_id}</strong>
      <span id="session-task-summary">{session.task_summary || ''}</span>
      {approvePushButton}
      <button
        id="session-stop"
        type="button"
        title="Stop the live Claude subprocess for this task"
        onClick={onStop}
        disabled={stopping || baseStatus !== TAB_STATUS.ACTIVE}
      >
        {stopLabel}
      </button>
    </header>
  );
}
