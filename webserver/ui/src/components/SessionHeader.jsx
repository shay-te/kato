import { useState } from 'react';
import { postSession } from '../api.js';
import { TAB_STATUS } from '../constants/tabStatus.js';
import { usePushApproval } from '../hooks/usePushApproval.js';
import { useTaskPublish } from '../hooks/useTaskPublish.js';
import { deriveTabStatus, resolveTabStatus, tabStatusTitle } from '../utils/tabStatus.js';
import { SESSION_LIFECYCLE } from '../hooks/useSessionStream.js';

export default function SessionHeader({
  session,
  needsAttention = false,
  onStopped,
  streamLifecycle,
  turnInFlight = false,
}) {
  const [stopping, setStopping] = useState(false);
  const pushApproval = usePushApproval(session?.task_id || '');
  const taskPublish = useTaskPublish(session?.task_id || '');
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

  const idleAlive = status === TAB_STATUS.ACTIVE
    && !turnInFlight
    && session?.working === false;
  const dotClass = [
    'status-dot',
    `status-${status}`,
    isLoading ? 'is-loading' : '',
    idleAlive ? 'is-idle-alive' : '',
  ].filter(Boolean).join(' ');
  const stopLabel = stopping ? 'Stopping…' : 'Stop';
  const pushLabel = pushApproval.busy ? 'Pushing…' : 'Approve push';
  const approvePushButton = pushApproval.awaiting && (
    <button
      id="session-approve-push"
      type="button"
      data-tooltip="Approve push: kato will push the branch and open the pull request."
      onClick={pushApproval.approve}
      disabled={pushApproval.busy}
    >
      {pushLabel}
    </button>
  );

  const claudeStatus = describeClaudeStatus(streamLifecycle, turnInFlight, baseStatus);
  // The Push button is *only* gated on "is there anything to push?" —
  // not on workspace existence, not on PR existence. When everything's
  // already on the remote we disable it (clicking would be a no-op);
  // otherwise it's clickable and worst case the click surfaces an
  // error the operator can act on.
  const pushDisabled = !taskPublish.hasChangesToPush || taskPublish.pushBusy;
  const pushTitle = pushTitleFor(taskPublish);
  const prDisabled = !taskPublish.hasWorkspace
    || taskPublish.hasPullRequest
    || taskPublish.prBusy;
  const prTitle = prTitleFor(taskPublish);

  return (
    <header id="session-header">
      <span
        id="session-status-dot"
        className={dotClass}
        title={tabStatusTitle(baseStatus, needsAttention)}
      />
      <strong id="session-task-id">{session.task_id}</strong>
      <span id="session-task-summary">{session.task_summary || ''}</span>
      <span
        id="session-claude-status"
        className={`claude-status claude-status-${claudeStatus.kind}`}
        title={claudeStatus.title}
      >
        Claude: {claudeStatus.label}
      </span>
      {approvePushButton}
      <button
        id="session-push"
        type="button"
        data-tooltip={pushTitle}
        onClick={taskPublish.push}
        disabled={pushDisabled}
      >
        {taskPublish.pushBusy ? 'Pushing…' : 'Push'}
      </button>
      <button
        id="session-pull-request"
        type="button"
        data-tooltip={prTitle}
        onClick={taskPublish.createPullRequest}
        disabled={prDisabled}
      >
        {taskPublish.prBusy ? 'Opening PR…' : 'Pull request'}
      </button>
      <button
        id="session-stop"
        type="button"
        data-tooltip="Stop the live Claude subprocess for this task. The chat history is preserved; kato can respawn Claude when you send the next message."
        onClick={onStop}
        disabled={stopping || baseStatus !== TAB_STATUS.ACTIVE}
      >
        {stopLabel}
      </button>
    </header>
  );
}

function pushTitleFor(state) {
  if (state.pushBusy) { return 'Push in progress…'; }
  if (!state.hasWorkspace) {
    return 'Nothing to push — kato has not provisioned a workspace for this task yet.';
  }
  if (!state.hasChangesToPush) {
    return 'Nothing to push — every repository is already in sync with its remote.';
  }
  return 'Push the current branch to its remote (no PR opened).';
}

function prTitleFor(state) {
  if (!state.hasWorkspace) {
    return 'No workspace yet — kato needs to provision the task before you can open a PR.';
  }
  if (state.hasPullRequest) {
    const url = (state.pullRequestUrls && state.pullRequestUrls[0]) || '';
    return url
      ? `Pull request already exists: ${url}`
      : 'Pull request already exists for this task.';
  }
  if (state.prBusy) { return 'Opening pull request…'; }
  return 'Push the branch and open a pull request.';
}

// Map (lifecycle, turnInFlight) → a short status word + tooltip for the
// Claude agent indicator. ``streamLifecycle`` is undefined when the
// header is rendered without a stream context (defensive — should not
// happen in normal use but keeps the chip from blowing up).
function describeClaudeStatus(streamLifecycle, turnInFlight, baseStatus) {
  if (baseStatus === TAB_STATUS.PROVISIONING) {
    return {
      kind: 'provisioning',
      label: 'provisioning',
      title: 'Workspace is being set up.',
    };
  }
  if (turnInFlight) {
    return {
      kind: 'working',
      label: 'working',
      title: 'Claude is processing the current turn.',
    };
  }
  switch (streamLifecycle) {
    case SESSION_LIFECYCLE.STREAMING:
      return {
        kind: 'idle',
        label: 'idle',
        title: 'Claude is connected and waiting for input.',
      };
    case SESSION_LIFECYCLE.CONNECTING:
      return {
        kind: 'connecting',
        label: 'connecting',
        title: 'Connecting to the Claude session…',
      };
    case SESSION_LIFECYCLE.IDLE:
      return {
        kind: 'sleeping',
        label: 'sleeping',
        title: 'No live subprocess — kato will respawn Claude on the next message.',
      };
    case SESSION_LIFECYCLE.CLOSED:
      return {
        kind: 'closed',
        label: 'closed',
        title: 'The Claude subprocess for this task has ended.',
      };
    case SESSION_LIFECYCLE.MISSING:
      return {
        kind: 'missing',
        label: 'no record',
        title: 'No record for this task on the server.',
      };
    default:
      return {
        kind: 'unknown',
        label: '—',
        title: 'Claude status unknown.',
      };
  }
}
