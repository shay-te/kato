import { TAB_STATUS } from '../constants/tabStatus.js';
import { deriveTabStatus, tabStatusTitle } from '../utils/tabStatus.js';

export default function Tab({ session, active, needsAttention, onSelect }) {
  const baseStatus = deriveTabStatus(session);
  const status = needsAttention ? TAB_STATUS.ATTENTION : baseStatus;
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
  return (
    <li
      className={className}
      data-task-id={session.task_id}
      onClick={() => onSelect(session.task_id)}
    >
      <span
        className={dotClass}
        title={tabStatusTitle(baseStatus, needsAttention)}
      />
      <strong>{session.task_id}</strong>
      <p>{session.task_summary || ''}</p>
    </li>
  );
}
