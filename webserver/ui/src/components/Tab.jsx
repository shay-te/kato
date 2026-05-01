import { TAB_STATUS } from '../constants/tabStatus.js';
import { deriveTabStatus, resolveTabStatus, tabStatusTitle } from '../utils/tabStatus.js';
import Icon from './Icon.jsx';

export default function Tab({ session, active, needsAttention, onSelect, onForget }) {
  const baseStatus = deriveTabStatus(session);
  const status = resolveTabStatus(session, needsAttention);
  const isLoading = baseStatus === TAB_STATUS.PROVISIONING;
  const className = [
    'tab',
    active ? 'active' : '',
    needsAttention ? 'needs-attention' : '',
  ].filter(Boolean).join(' ');
  const dotClass = [
    'status-dot',
    `status-${status}`,
    isLoading ? 'is-loading' : '',
  ].filter(Boolean).join(' ');
  function handleSelect() {
    onSelect(session.task_id);
  }
  function handleForget(event) {
    event.stopPropagation();
    if (typeof onForget !== 'function') { return; }
    const ok = window.confirm(
      `Forget task ${session.task_id}?\n\n`
      + 'This deletes the local workspace folder and removes the tab. '
      + 'Anything not already pushed will be lost.',
    );
    if (ok) { onForget(session.task_id); }
  }
  const hasChangesPending = !!session.has_changes_pending;
  const changesIndicator = hasChangesPending && (
    <span
      className="tab-changes-indicator"
      title="Changes ready to push — kato is waiting for your approval"
    >
      <Icon name="commit" />
    </span>
  );
  return (
    <li
      className={className}
      data-task-id={session.task_id}
      onClick={handleSelect}
    >
      <span
        className={dotClass}
        title={tabStatusTitle(baseStatus, needsAttention)}
      />
      <strong>{session.task_id}</strong>
      <p>{session.task_summary || ''}</p>
      {changesIndicator}
      <button
        type="button"
        className="tab-forget-btn"
        title="Forget this task — delete the local workspace and clear this tab"
        onClick={handleForget}
      >
        <Icon name="xmark" />
      </button>
    </li>
  );
}
