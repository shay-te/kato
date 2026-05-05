import { useState } from 'react';
import { finishTask, postSession, updateTaskSource } from '../api.js';
import { TAB_STATUS } from '../constants/tabStatus.js';
import { usePushApproval } from '../hooks/usePushApproval.js';
import { useTaskPublish } from '../hooks/useTaskPublish.js';
import { deriveTabStatus, resolveTabStatus, tabStatusTitle } from '../utils/tabStatus.js';
import { SESSION_LIFECYCLE } from '../hooks/useSessionStream.js';
import { toast } from '../stores/toastStore.js';
import AdoptSessionModal from './AdoptSessionModal.jsx';

export default function SessionHeader({
  session,
  needsAttention = false,
  onStopped,
  onResume,
  onSessionAdopted,
  streamLifecycle,
  turnInFlight = false,
}) {
  const [stopping, setStopping] = useState(false);
  const [resuming, setResuming] = useState(false);
  const [finishing, setFinishing] = useState(false);
  const [updatingSource, setUpdatingSource] = useState(false);
  const [adoptModalOpen, setAdoptModalOpen] = useState(false);
  const pushApproval = usePushApproval(session?.task_id || '');
  const taskPublish = useTaskPublish(session?.task_id || '');
  if (!session) { return null; }
  const baseStatus = deriveTabStatus(session);
  const status = resolveTabStatus(session, needsAttention);
  const isLoading = baseStatus === TAB_STATUS.PROVISIONING;
  // Session is "resumable" when the streaming subprocess isn't
  // running — the operator stopped it, it ended on its own, or the
  // tab loaded against a record with no live process. In those
  // states the Stop button morphs into Resume so the operator has
  // an explicit way to respawn (instead of typing "please continue"
  // into the chat as a workaround).
  const isResumable = (
    streamLifecycle === SESSION_LIFECYCLE.CLOSED
    || streamLifecycle === SESSION_LIFECYCLE.IDLE
    || streamLifecycle === SESSION_LIFECYCLE.MISSING
  );

  async function onStop() {
    setStopping(true);
    const result = await postSession(session.task_id, 'stop');
    setStopping(false);
    if (typeof onStopped === 'function') {
      onStopped(result);
    }
  }

  async function onResumeClick() {
    if (resuming) { return; }
    if (typeof onResume !== 'function') { return; }
    setResuming(true);
    try {
      await onResume();
    } finally {
      setResuming(false);
    }
  }

  async function onUpdateSource() {
    if (updatingSource) { return; }
    setUpdatingSource(true);
    const result = await updateTaskSource(session.task_id);
    setUpdatingSource(false);
    if (typeof taskPublish.refresh === 'function') {
      taskPublish.refresh();
    }
    const { title, message } = formatUpdateSourceResult(result);
    const body = (result && result.body) || {};
    const failed = (body.failed_repositories || []).length;
    const updated = (body.updated_repositories || []).length;
    const warnings = body.warnings || [];
    // Stash conflicts (or any warning) downgrade success → warning
    // so the toast is yellow, not green — operator should see it
    // and act on the conflict markers in the working tree.
    const hasWarnings = warnings.length > 0;
    let kind;
    if (!result.ok || failed > 0) {
      kind = updated > 0 ? 'warning' : 'error';
    } else if (hasWarnings) {
      kind = 'warning';
    } else {
      kind = 'success';
    }
    toast.show({
      kind,
      title,
      message,
      durationMs: kind === 'error' ? 12000 : 8000,
    });
  }

  async function onFinish() {
    if (finishing) { return; }
    setFinishing(true);
    const result = await finishTask(session.task_id);
    setFinishing(false);
    // Force a publish-state refresh so the Push/PR buttons reflect
    // the new state immediately (PR exists, nothing to push).
    if (typeof taskPublish.refresh === 'function') {
      taskPublish.refresh();
    }
    // Toast classification: full success → green, partial → amber,
    // request-level failure → red. Multi-line message is fine — the
    // toast component renders <pre> and wraps long lines.
    const { title, message } = formatFinishResult(result);
    const body = (result && result.body) || {};
    const kind = !result.ok
      ? 'error'
      : body.finished
        ? 'success'
        : 'warning';
    toast.show({
      kind,
      title,
      message,
      durationMs: kind === 'error' ? 12000 : 7000,
    });
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
  const resumeLabel = resuming ? 'Resuming…' : 'Resume';
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

  const claudeStatus = describeClaudeStatus(
    streamLifecycle,
    turnInFlight,
    baseStatus,
    needsAttention,
  );
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
    <>
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
          id="session-update-source"
          type="button"
          data-tooltip="Update source — push the task branch, then for each repo under REPOSITORY_ROOT_PATH: fetch, checkout the task branch, and pull. Lets you test the task on your live running system. Refuses if a source repo has uncommitted changes."
          onClick={onUpdateSource}
          disabled={updatingSource}
        >
          {updatingSource ? 'Updating source…' : 'Update source'}
        </button>
        <button
          id="session-finish"
          type="button"
          data-tooltip="Done — push pending changes, open a PR if missing, and move the ticket to In Review. Same flow Claude can trigger by emitting <KATO_TASK_DONE>."
          onClick={onFinish}
          disabled={finishing}
        >
          {finishing ? 'Finishing…' : 'Done'}
        </button>
        <button
          id="session-adopt-claude"
          type="button"
          data-tooltip="Adopt an existing Claude Code session for this task — e.g. a chat you already started in the VS Code extension. Kato will --resume that session on the next agent spawn instead of starting fresh."
          onClick={() => setAdoptModalOpen(true)}
        >
          Adopt session
        </button>
        {isResumable ? (
          <button
            id="session-resume"
            type="button"
            data-tooltip="Resume the Claude session — kato will respawn the subprocess and ask Claude to pick up where it left off."
            onClick={onResumeClick}
            disabled={resuming || typeof onResume !== 'function'}
          >
            {resumeLabel}
          </button>
        ) : (
          <button
            id="session-stop"
            type="button"
            data-tooltip="Stop the live Claude subprocess for this task. The chat history is preserved; you can resume from this header when the subprocess has ended."
            onClick={onStop}
            disabled={stopping || baseStatus !== TAB_STATUS.ACTIVE}
          >
            {stopLabel}
          </button>
        )}
      </header>
      {adoptModalOpen && (
        <AdoptSessionModal
          taskId={session.task_id}
          onClose={() => setAdoptModalOpen(false)}
          onAdopted={(adopted) => {
            setAdoptModalOpen(false);
            if (typeof onSessionAdopted === 'function') {
              onSessionAdopted(adopted);
            }
          }}
        />
      )}
    </>
  );
}

// Render the per-repo outcome of POST /update-source into a toast.
// Tells the operator exactly which source clones now reflect the
// task branch and which were skipped (dirty / missing) or failed.
function formatUpdateSourceResult(result) {
  const body = (result && result.body) || {};
  if (!result || !result.ok) {
    return {
      title: 'Update source failed',
      message: (result && result.error)
        || body.error
        || JSON.stringify(body, null, 2)
        || 'unknown error',
    };
  }
  const lines = [];
  const push = body.pushed || {};
  const pushedCount = (push.pushed_repositories || []).length;
  const pushSkipped = (push.skipped_repositories || []).length;
  const pushFailed = (push.failed_repositories || []).length;
  if (pushedCount) {
    lines.push(`✓ pushed ${pushedCount} repo(s) to remote`);
  } else if (pushSkipped) {
    lines.push(`• push skipped — already in sync (${pushSkipped} repo(s))`);
  } else if (pushFailed) {
    const errs = (push.failed_repositories || [])
      .map((r) => `${r.repository_id}: ${r.error}`).join('; ');
    lines.push(`✗ push failed: ${errs}`);
  }
  const updated = body.updated_repositories || [];
  if (updated.length) {
    lines.push(`✓ source updated for ${updated.length} repo(s): ${updated.join(', ')}`);
  }
  // Per-repo warnings (e.g. "stashed your changes; reapplied with
  // conflicts"). Each warning means the repo DID update, but the
  // operator needs to look at it.
  const warnings = body.warnings || [];
  for (const entry of warnings) {
    const marker = entry.stash_conflict ? '⚠' : '•';
    const text = String(entry.warning || '').trim();
    if (text) {
      lines.push(`${marker} ${text}`);
    }
  }
  const skipped = body.skipped_repositories || [];
  for (const entry of skipped) {
    lines.push(`• skipped ${entry.repository_id}: ${entry.reason}`);
  }
  const failed = body.failed_repositories || [];
  for (const entry of failed) {
    lines.push(`✗ ${entry.repository_id}: ${entry.error}`);
  }
  if (!updated.length && !failed.length && !skipped.length) {
    lines.push('• no source repositories updated');
  }
  const title = body.updated
    ? (failed.length ? 'Source partially updated' : 'Source updated')
    : 'Source not updated';
  return { title, message: lines.join('\n') };
}

// Render the per-step outcome of POST /finish into a toast title +
// multi-line message. Goal: never leave the operator guessing
// whether *anything* happened — every step (push, PR, move-to-review)
// gets one line, with the failure reason inline when a step didn't
// run or errored.
function formatFinishResult(result) {
  const body = (result && result.body) || {};
  if (!result || !result.ok) {
    return {
      title: 'Finish request failed',
      message: (result && result.error)
        || JSON.stringify(body, null, 2)
        || 'unknown error',
    };
  }
  const lines = [];
  const push = body.pushed || {};
  const pushedCount = (push.pushed_repositories || []).length;
  const pushSkipped = (push.skipped_repositories || []).length;
  const pushFailed = (push.failed_repositories || []).length;
  if (pushedCount) {
    lines.push(`✓ pushed ${pushedCount} repo(s): ${(push.pushed_repositories || []).join(', ')}`);
  } else if (pushSkipped) {
    lines.push(`• push skipped — nothing to push (${pushSkipped} repo(s) already in sync)`);
  } else if (pushFailed) {
    const errs = (push.failed_repositories || [])
      .map((r) => `${r.repository_id}: ${r.error}`).join('; ');
    lines.push(`✗ push failed: ${errs}`);
  } else {
    lines.push(`• push: ${push.error || 'no action'}`);
  }
  const pr = body.pull_request || {};
  const prCreated = (pr.created_pull_requests || []).length;
  const prSkipped = (pr.skipped_existing || []).length;
  const prFailed = (pr.failed_repositories || []).length;
  if (prCreated) {
    const urls = (pr.created_pull_requests || [])
      .map((r) => r.url || r.repository_id).join(', ');
    lines.push(`✓ opened ${prCreated} pull request(s): ${urls}`);
  } else if (prSkipped) {
    lines.push(`• PR skipped — already exists for ${prSkipped} repo(s)`);
  } else if (prFailed) {
    const errs = (pr.failed_repositories || [])
      .map((r) => `${r.repository_id}: ${r.error}`).join('; ');
    lines.push(`✗ PR failed: ${errs}`);
  } else {
    lines.push(`• pull request: ${pr.error || 'no action'}`);
  }
  if (body.moved_to_review) {
    lines.push('✓ ticket moved to In Review');
  } else {
    const why = body.move_error || 'unknown reason — check kato logs';
    lines.push(`✗ ticket did NOT move to In Review: ${why}`);
  }
  return {
    title: body.finished
      ? 'Done — task finalised'
      : 'Done — partial completion',
    message: lines.join('\n'),
  };
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
function describeClaudeStatus(
  streamLifecycle,
  turnInFlight,
  baseStatus,
  needsAttention,
) {
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
  if (needsAttention) {
    return {
      kind: 'approval',
      label: 'approval',
      title: 'Claude is paused waiting for your approval.',
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
