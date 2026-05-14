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
  // ``status === active`` covers two real states the sidebar should
  // distinguish: Claude actively producing a turn (bright green) vs
  // alive-but-idle (dimmed green). Backend exposes a per-session
  // ``working`` flag (turn in flight) that we use here so the operator
  // can tell at a glance whether the agent is still chewing on a tab
  // they're not currently looking at.
  const idleAlive = status === TAB_STATUS.ACTIVE && session?.working === false;
  const dotClass = [
    'status-dot',
    `status-${status}`,
    isLoading ? 'is-loading' : '',
    idleAlive ? 'is-idle-alive' : '',
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
  // Tooltip shown on hover: full task summary (which is ellipsized
  // in the pill so wide tabs don't push neighbours off-screen) plus
  // the status description.
  //
  // We use the native ``title`` attribute (not our custom
  // ``data-tooltip`` system) because the tab strip lives inside
  // ``.tabs-scroller`` which has ``overflow: hidden`` to clip
  // sideways-scrolled segments — that same overflow eats any
  // CSS-absolute tooltip pseudo before it reaches the visible
  // page. Native titles are rendered by the OS outside the page
  // box, so the clip can't touch them.
  const tabTooltip = composeTabTooltip(session, baseStatus, needsAttention);
  return (
    <li
      className={className}
      data-task-id={session.task_id}
      title={tabTooltip}
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
        className="tab-forget-btn tooltip-end"
        data-tooltip="Forget this task — delete the local workspace and clear this tab. Anything not pushed will be lost."
        aria-label="Forget this task"
        onClick={handleForget}
      >
        <Icon name="xmark" />
      </button>
    </li>
  );
}


// Build the hover-tooltip body for a tab. Lives here (not in
// tabStatusTitle) because it pulls per-tab data — summary, repos,
// pending-changes flag — that the status-title helper doesn't see.
//
// Single line with `` · `` separators rather than newlines:
// browsers disagree on whether ``\n`` inside a ``title`` attribute
// renders as a line break (Safari + Firefox: yes; Chrome on Mac:
// no — strips them). The bullet keeps the meaning intact across
// all three.
function composeTabTooltip(session, baseStatus, needsAttention) {
  const taskId = String(session?.task_id || '').trim();
  const summary = String(session?.task_summary || '').trim();
  const statusLine = tabStatusTitle(baseStatus, needsAttention);
  const parts = [];
  if (taskId && summary) {
    parts.push(`${taskId} — ${summary}`);
  } else if (taskId) {
    parts.push(taskId);
  } else if (summary) {
    parts.push(summary);
  }
  if (statusLine) { parts.push(statusLine); }
  if (session?.has_changes_pending) {
    parts.push('Changes ready to push — waiting for your approval');
  }
  return parts.join(' · ') || 'Task';
}
