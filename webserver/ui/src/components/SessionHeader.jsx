import { useState } from 'react';
import {
  finishTask,
  mergeDefaultBranch,
  postChatMessage,
  postSession,
  triggerScan,
  updateTaskSource,
} from '../api.js';
import { AGENT_SESSION_ID } from '../constants/sessionFields.js';
import { promptStore } from '../stores/promptStore.js';
import { useBusyAction } from '../hooks/useBusyAction.js';
import { usePushApproval } from '../hooks/usePushApproval.js';
import { useTaskPublish } from '../hooks/useTaskPublish.js';
import { cx } from '../utils/cx.js';
import { lastActionSuffix, recordGitActionNow } from '../utils/lastGitAction.js';
import { deriveTabStatus, tabStatusTitle } from '../utils/tabStatus.js';
import { deriveAgentStatus } from '../utils/agentStatus.js';
import { SESSION_LIFECYCLE } from '../hooks/useSessionStream.js';
import { toast, toastResult } from '../stores/toastStore.js';
import { backendLabel } from './AgentBackendChip.jsx';
import { useActiveBackend } from '../stores/activeBackendStore.js';
import AdoptSessionModal from './AdoptSessionModal.jsx';
import Icon, { BusyIcon } from './Icon.jsx';
import {
  formatFinishResult,
  formatMergeResult,
  formatMergeConflicts,
  formatPullResult,
  formatUpdateSourceResult,
} from './sessionHeaderFormatters.js';

export default function SessionHeader({
  session,
  needsAttention = false,
  onStopped,
  onResume,
  onSessionAdopted,
  onChatChanged = null,
  onChatSwitchPending = null,
  streamLifecycle,
  turnInFlight = false,
  awaitingBackground = false,
  backgroundIsWorkflow = false,
  searchSlot = null,
  onSendPrompt = null,
  onWorkspaceMutated = null,
}) {
  const [resuming, setResuming] = useState(false);
  const [adoptModalOpen, setAdoptModalOpen] = useState(false);
  const pushApproval = usePushApproval(session?.task_id || '');
  const taskPublish = useTaskPublish(session?.task_id || '');

  // Manual scan trigger — fires the autonomous scan job NOW so the
  // operator doesn't have to wait for the 3-minute auto-tick.
  // Refreshes review comments + task status for THIS task (and
  // every other live task as a side effect — the underlying job
  // iterates all assigned + review tasks). Keeps the operator in
  // control of when provider APIs (Bitbucket / GitHub / GitLab)
  // get hit, instead of the old 30s firehose.
  const [syncing, onSyncNow] = useBusyAction(async () => {
    const result = await triggerScan();
    if (result.ok) {
      toast.show({
        kind: 'success',
        title: 'Scan triggered',
        message: 'Kato is checking for new tasks, status changes, and review comments.',
      });
    } else {
      toast.errorFromResult(result, {
        title: 'Scan failed', fallback: 'unknown error', durationMs: 5000,
      });
    }
  });

  // Stop the live subprocess, then hand the result up to the parent.
  const [stopping, onStop] = useBusyAction(
    () => postSession(session.task_id, 'stop'),
    {
      onDone: (result) => {
        if (typeof onStopped === 'function') {
          onStopped(result);
        }
      },
    },
  );

  // Fetch + merge the repo's default branch into the task branch
  // (the agent's clone can't run git itself). On conflict the
  // markers are left in the tree and we tell the chat agent —
  // listing the exact files — to resolve them.
  const [mergingDefault, onMergeDefault] = useBusyAction(
    () => mergeDefaultBranch(session.task_id),
    {
      onDone: async (result) => {
        // Record that the operator ran a merge from here (shown in the merge
        // tooltip) — before the refresh so the re-render reads the new time.
        recordGitActionNow(session.task_id, 'merge');
        if (typeof taskPublish.refresh === 'function') {
          taskPublish.refresh();
        }
        const body = result.body || {};
        if (!result.ok && !body.has_conflicts && !body.merged) {
          toast.errorFromResult(result, {
            title: 'Merge failed', fallback: 'merge failed', durationMs: 12000,
          });
          return;
        }
        // The merge mutated the workspace clone on disk — a clean merge
        // brought origin/<default> in, a conflicted one left markers in
        // the tree. Bump the workspace version so the Changes tab / Files
        // tree / open editor refetch instead of showing pre-merge content.
        if (typeof onWorkspaceMutated === 'function') {
          onWorkspaceMutated(session.task_id);
        }
        const conflicted = Array.isArray(body.conflicted_repositories)
          ? body.conflicted_repositories : [];
        if (conflicted.length > 0) {
          const fileLines = conflicted.flatMap((repo) =>
            (repo.conflicted_files || []).map(
              (f) => `- ${repo.repository_id}: ${f}`,
            ),
          );
          const defaultBranch =
            conflicted[0]?.default_branch || 'the default branch';
          // Tell the agent to resolve — it can't run git, but it CAN
          // edit the conflicted files. kato's normal commit/push then
          // finalises the merge.
          const instruction =
            `I merged origin/${defaultBranch} into this task branch and `
            + `there are merge conflicts. The clone can't run git, so do `
            + `NOT try git commands — just edit these files to resolve `
            + `every conflict (remove all <<<<<<< / ======= / >>>>>>> `
            + `markers, keeping both sides' intent where it makes sense), `
            + `then continue:\n${fileLines.join('\n')}`;
          const sent = await postChatMessage(session.task_id, instruction);
          // The toast NAMES each conflicted repository (+ file count) so the
          // operator knows where to look; the per-file resolution went to
          // Claude above.
          toast.show({
            ...formatMergeConflicts(conflicted, {
              chatDelivered: !!(sent && sent.ok),
              taskId: session.task_id,
            }),
            durationMs: 12000,
          });
          return;
        }
        // Clean / skipped / failed / nothing — one toast that NAMES every
        // repo merged from the default branch (formatMergeResult). The
        // conflict path above is handled separately (it messages Claude).
        const merged = formatMergeResult(result, session.task_id);
        toast.show({ ...merged, durationMs: merged.kind === 'success' ? 7000 : 6000 });
      },
    },
  );

  const [updatingSource, onUpdateSource] = useBusyAction(
    () => updateTaskSource(session.task_id),
    {
      onDone: (result) => {
        if (typeof taskPublish.refresh === 'function') {
          taskPublish.refresh();
        }
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
        // Non-error duration stays 8000 (longer than the shared 7000
        // default) — update-source toasts are denser, operators need
        // the extra read time.
        toastResult(
          { ...formatUpdateSourceResult(result), kind },
          { defaultMs: 8000 },
        );
      },
    },
  );

  const [finishing, onFinish] = useBusyAction(
    () => finishTask(session.task_id),
    {
      onDone: (result) => {
        // Force a publish-state refresh so the Push/PR buttons reflect
        // the new state immediately (PR exists, nothing to push).
        if (typeof taskPublish.refresh === 'function') {
          taskPublish.refresh();
        }
        // Toast classification: full success → green, partial → amber,
        // request-level failure → red. Multi-line message is fine — the
        // toast component renders <pre> and wraps long lines.
        const body = (result && result.body) || {};
        const kind = !result.ok
          ? 'error'
          : body.finished
            ? 'success'
            : 'warning';
        toastResult({ ...formatFinishResult(result, session.task_id), kind });
      },
    },
  );

  // "Code review" — routes the detailed PR-review prompt through the SAME
  // composer send path (onSendPrompt → SessionDetail.onSendMessage) as
  // typing it: it shows in the chat, wakes a sleeping session (reconnects
  // the stream on respawn), and queues if Claude is mid-turn. A raw
  // postChatMessage skipped all of that, so on a sleeping session nothing
  // appeared.
  const [reviewing, onCodeReview] = useBusyAction(
    () => (typeof onSendPrompt === 'function'
      ? onSendPrompt(promptStore.get('codeReview'))
      : Promise.resolve(false)),
    {
      onDone: (delivered) => {
        toast.show(delivered
          ? {
            kind: 'success',
            title: 'Code review requested',
            message: `Sent the review prompt to ${agentName} (queued if it’s mid-turn).`,
            durationMs: 5000,
          }
          : {
            kind: 'error',
            title: 'Couldn’t request review',
            message: 'The chat didn’t accept the prompt — try again.',
            durationMs: 6000,
          });
      },
    },
  );

  if (!session) { return null; }
  const baseStatus = deriveTabStatus(session);
  const agentSessionId = session[AGENT_SESSION_ID] || '';
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

  async function onPull() {
    if (taskPublish.pullBusy) { return; }
    const result = await taskPublish.pull();
    if (typeof taskPublish.refresh === 'function') {
      taskPublish.refresh();
    }
    // A fast-forward pull advances the workspace clone on disk — bump
    // the workspace version so the Changes tab / Files tree / open
    // editor refetch the new content instead of staying stale.
    if (typeof onWorkspaceMutated === 'function') {
      onWorkspaceMutated(session.task_id);
    }
    toastResult(formatPullResult(result));
  }

  // Open the task's pull request(s) on the provider in new browser
  // tabs. Multi-repo tasks can have one PR per repo, so open them
  // all (the click is a direct user gesture, so the browser allows
  // the batch). ``noopener,noreferrer`` keeps the opened pages from
  // reaching back into the planning UI.
  function onOpenPullRequest() {
    if (prUrls.length === 0) { return; }
    prUrls.forEach((url) => {
      window.open(url, '_blank', 'noopener,noreferrer');
    });
  }

  // One derivation drives BOTH the dot and the chip — the same value the tab
  // surfaces use — so the header and tab can never disagree (UNA-2492). The
  // header is always the active task, so the live stream feeds it directly.
  // ``awaitingBackground`` MUST be included: liveKind counts it as "working"
  // (a Monitor / run_in_background wait), and the tab badge already passes it
  // via agentStatusStore — omitting it here made the header read "idle" while
  // the tab read "working" during a background wait.
  // One name for every agent-facing string in this header — the status
  // pill, the resume/stop tooltips, the review and Done copy.
  // 'Agent' (capitalised) not 'the agent': it is used as a NAME in the
  // status pill ('Agent: working') as well as in prose.
  // The tab the operator is ON, not the polled record — the same
  // substitution that sent Codex-tab messages tagged "claude".
  const activeBackend = useActiveBackend(
    session?.task_id || '', session?.agent_backend || '',
  );
  const agentName = backendLabel(activeBackend) || 'Agent';
  // The OTHER agents' status is shown on their own tabs
  // (AgentBackendTabs), beside the name it describes. A second row of
  // chips up here restated the same facts one surface away from what
  // they referred to.

  const agent = deriveAgentStatus(
    session,
    { lifecycle: streamLifecycle, turnInFlight, awaitingBackground, backgroundIsWorkflow },
    needsAttention,
    agentName,
  );
  const stopLabel = stopping ? 'Stopping…' : 'Stop';
  const resumeLabel = resuming ? 'Resuming…' : 'Resume';
  const pushLabel = pushApproval.busy ? 'Pushing…' : 'Approve push';
  const approvePushButton = pushApproval.awaiting && (
    <button
      id="session-approve-push"
      type="button"
      className="session-action"
      data-tooltip="Approve push: kato will push the branch and open the pull request."
      onClick={pushApproval.approve}
      disabled={pushApproval.busy}
      aria-label={pushLabel}
    >
      <BusyIcon busy={pushApproval.busy} idle="check" />
    </button>
  );

  // ── Git action buttons: ONE operation at a time ─────────────────────────
  // Push / Pull / Merge share a single gate — enabled only when (1) kato has
  // reported this task's state (ready), (2) the repos are provisioned (a
  // clone exists), and (3) NO other git op is already running (so you can't,
  // e.g., pull while a merge is mid-flight). Deliberately NO per-button "is
  // there anything to do?" pre-check: the buttons stay clickable, repeat
  // clicks are fine (a no-op just toasts "already up to date"), and each
  // tooltip shows when you last ran it — no remote-state guessing.
  const anyGitOpBusy = taskPublish.pushBusy || taskPublish.pullBusy
    || taskPublish.prBusy || mergingDefault || updatingSource;
  let gitBlockedReason = '';  // '' → ready to run a git op
  if (!taskPublish.publishStateReady && !taskPublish.publishStateError) {
    gitBlockedReason = "Loading this task's git status — one moment…";
  } else if (taskPublish.publishStateError) {
    gitBlockedReason = "Can't load this task's git status — the server isn't "
      + 'responding. Reopen the task to retry.';
  } else if (!taskPublish.hasWorkspace) {
    gitBlockedReason = 'No git workspace clone for this task — nothing to act on.';
  } else if (anyGitOpBusy) {
    gitBlockedReason = 'Another git action is already running — only one at a time.';
  }
  const gitDisabled = gitBlockedReason !== '';
  const pushTitle = gitBlockedReason
    || 'Push this task branch to its remote (safe to click again — a no-op if '
      + 'everything is already pushed).'
      + lastActionSuffix(session.task_id, 'push', 'pushed');
  const pullTitle = gitBlockedReason
    || 'Pull the task branch from its remote into the workspace clone (safe to '
      + 'click again — the toast reports "already in sync" if there is nothing).'
      + lastActionSuffix(session.task_id, 'pull', 'pulled');
  const mergeTitle = gitBlockedReason
    || ('Merge the default branch (master/main) into this task branch. '
      + "The agent's clone can't run git, so use this when the branch fell behind "
      + `— on conflict the markers are left in place and ${agentName} is told (with the `
      + 'file list) to resolve them. Safe to click again ("already up to date" if '
      + 'nothing new).'
      + lastActionSuffix(session.task_id, 'merge', 'merged'));
  // PR + Update-source also honour "one git op at a time" (gitDisabled), on
  // top of their own guards (PR: skip when every repo already has one).
  const prDisabled = gitDisabled || taskPublish.hasPullRequest;
  const prTitle = prTitleFor(taskPublish);
  const prUrls = Array.isArray(taskPublish.pullRequestUrls)
    ? taskPublish.pullRequestUrls.filter(Boolean) : [];
  const openPrDisabled = prUrls.length === 0;
  let openPrTitle;
  if (openPrDisabled) {
    openPrTitle = 'No pull request yet — open one with the adjacent '
      + 'Pull request button (or Done) first.';
  } else if (prUrls.length === 1) {
    openPrTitle = 'Open the pull request on the provider in a new '
      + 'browser tab.';
  } else {
    openPrTitle = `Open all ${prUrls.length} pull requests `
      + '(one per repository) in new browser tabs.';
  }
  // Per AGENTS.md "no logic inside JSX": every label / element /
  // condition that the return statement consumes is precomputed
  // here so the JSX below is pure rendering.
  const taskSummary = session.task_summary || '';
  // Session id chip lives only next to the ``Claude: <status>`` pill on
  // the right. It used to ALSO sit beside the task id on the left, but
  // that crowded the task code/title, so it was removed there.
  const sessionIdBadgeRight = agentSessionId ? (
    <span
      className="claude-session-id is-aside-status"
      title={
        `Agent session id: ${agentSessionId}\n`
        + 'Resumed across restarts — same string on the chat panel.'
      }
    >
      sid:{agentSessionId.slice(0, 8)}…
    </span>
  ) : null;
  const pushButtonLabel = taskPublish.pushBusy ? 'Pushing…' : 'Push';
  const pullButtonLabel = taskPublish.pullBusy ? 'Pulling…' : 'Pull';
  const prButtonLabel = taskPublish.prBusy ? 'Opening PR…' : 'Pull request';
  // Update-source pushes the task branch then pulls each source repo — also a
  // git op, so it honours the same one-at-a-time gate.
  const updateSourceDisabled = gitDisabled;
  const updateSourceTitle = !taskPublish.hasWorkspace
    ? 'No workspace for this task — workspace must be provisioned before source can be updated.'
    : 'Update source — push the task branch, then for each repo under REPOSITORY_ROOT_PATH: fetch, checkout the task branch, and pull. Lets you test the task on your live running system. Refuses if a source repo has uncommitted changes.';
  const updateSourceLabel = updatingSource ? 'Updating source…' : 'Update source';
  const finishLabel = finishing ? 'Finishing…' : 'Done';
  const stopOrResumeButton = isResumable ? (
    <button
      id="session-resume"
      type="button"
      className="session-action is-warning"
      data-tooltip={`Resume the ${agentName} session — kato will respawn the subprocess and ask ${agentName} to pick up where it left off.`}
      onClick={onResumeClick}
      disabled={resuming || typeof onResume !== 'function'}
      aria-label={resumeLabel}
    >
      <BusyIcon busy={resuming} idle="play" />
    </button>
  ) : (
    <button
      id="session-stop"
      type="button"
      className="session-action is-danger"
      data-tooltip={`Stop the live ${agentName} subprocess for this task. The chat history is preserved; you can resume from this header when the subprocess has ended.`}
      onClick={onStop}
      // Enabled whenever this Stop variant is rendered. The
      // ``isResumable`` branch above already swapped to Resume when
      // the subprocess isn't live — so if we're rendering Stop, the
      // subprocess IS alive and stoppable. The previous
      // ``baseStatus !== ACTIVE`` guard silently DISABLED Stop while
      // Claude was WORKING (the exact moment operators want to use
      // it) because ``deriveTabStatus`` flips to ``WORKING`` while
      // ``session.working === true`` — the bug the operator
      // reported as "stop button doesn't stop the work".
      disabled={stopping}
      aria-label={stopLabel}
    >
      <BusyIcon busy={stopping} idle="stop" />
    </button>
  );
  const adoptModal = adoptModalOpen ? (
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
  ) : null;

  return (
    <>
      <header id="session-header">
        <div className="session-header-info">
          <span
            id="session-status-dot"
            className={agent.dotClass}
            title={tabStatusTitle(baseStatus, needsAttention)}
          />
          <strong id="session-task-id">{session.task_id}</strong>
          <span id="session-task-summary">{taskSummary}</span>
        </div>
        <div className="session-header-actions">
          {/* No status chip here. It lives ON each agent tab now, beside the
              name it describes — a chip up here could only ever describe one
              agent, and duplicated what the tab already says. */}
          {sessionIdBadgeRight}
          {searchSlot}
          {approvePushButton}
          <button
            id="session-code-review"
            type="button"
            className="session-action"
            data-tooltip={`Code review — ask ${agentName} to strictly review this task's changes (correctness, security, tests, redundancy, comment cleanup) and fix blockers before the PR.`}
            onClick={onCodeReview}
            disabled={reviewing}
            aria-label={reviewing ? 'Requesting review…' : 'Code review'}
          >
            <BusyIcon busy={reviewing} idle="diff" />
          </button>
          <button
            id="session-push"
            type="button"
            className="session-action"
            data-tooltip={pushTitle}
            onClick={taskPublish.push}
            disabled={gitDisabled}
            aria-label={pushButtonLabel}
          >
            <BusyIcon busy={taskPublish.pushBusy} idle="arrow-up" />
          </button>
          <button
            id="session-merge-default"
            type="button"
            className="session-action"
            data-tooltip={mergeTitle}
            onClick={onMergeDefault}
            disabled={gitDisabled}
            aria-label={mergingDefault ? 'Merging…' : 'Merge default branch'}
          >
            <BusyIcon busy={mergingDefault} idle="merge" />
          </button>
          <button
            id="session-pull"
            type="button"
            className="session-action"
            data-tooltip={pullTitle}
            onClick={onPull}
            disabled={gitDisabled}
            aria-label={pullButtonLabel}
          >
            <BusyIcon busy={taskPublish.pullBusy} idle="arrow-down" />
          </button>
          <button
            id="session-pull-request"
            type="button"
            className="session-action"
            data-tooltip={prTitle}
            onClick={taskPublish.createPullRequest}
            disabled={prDisabled}
            aria-label={prButtonLabel}
          >
            <BusyIcon busy={taskPublish.prBusy} idle="pull-request" />
          </button>
          <button
            id="session-open-pull-request"
            type="button"
            className="session-action"
            data-tooltip={openPrTitle}
            onClick={onOpenPullRequest}
            disabled={openPrDisabled}
            aria-label="Open pull request in a new tab"
          >
            <Icon name="external-link" />
          </button>
          <button
            id="session-update-source"
            type="button"
            className="session-action"
            data-tooltip={updateSourceTitle}
            onClick={onUpdateSource}
            disabled={updateSourceDisabled}
            aria-label={updateSourceLabel}
          >
            <BusyIcon busy={updatingSource} idle="refresh" />
          </button>
          <button
            id="session-finish"
            type="button"
            className="session-action is-primary"
            data-tooltip={`Done — push pending changes, open a PR if missing, and move the ticket to In Review. Same flow ${agentName} can trigger by emitting <KATO_TASK_DONE>.`}
            onClick={onFinish}
            disabled={finishing}
            aria-label={finishLabel}
          >
            <BusyIcon busy={finishing} idle="check" />
          </button>
          <button
            id="session-sync"
            type="button"
            className="session-action"
            data-tooltip="Sync now — run a scan immediately to pick up new review comments, status changes, and PR updates without waiting for the next 3-minute auto-tick."
            onClick={onSyncNow}
            disabled={syncing}
            aria-label={syncing ? 'Syncing…' : 'Sync now'}
          >
            <BusyIcon busy={syncing} idle="history" />
          </button>
          <button
            id="session-adopt-claude"
            type="button"
            className="session-action"
            data-tooltip="Adopt an existing Claude Code session for this task — e.g. a chat you already started in the VS Code extension. Kato will --resume that session on the next agent spawn instead of starting fresh."
            onClick={() => setAdoptModalOpen(true)}
            aria-label="Adopt session"
          >
            <Icon name="link" />
          </button>
          {stopOrResumeButton}
        </div>
      </header>
      {adoptModal}
    </>
  );
}

// Persistent header shown when NO task is selected. The bar must
// never disappear (a header that hides/shows as you click around is
// jarring) — so we keep the exact same shell, show a "Select a task"
// title on the left, and render the full action row on the right but
// inert (disabled + not focusable). No layout jump when a task is
// then selected and the real SessionHeader takes over.
export function SessionHeaderPlaceholder() {
  const buttons = [
    { icon: 'search', label: 'Search' },
    { icon: 'arrow-up', label: 'Push' },
    { icon: 'merge', label: 'Merge default branch' },
    { icon: 'arrow-down', label: 'Pull' },
    { icon: 'pull-request', label: 'Open pull request' },
    { icon: 'external-link', label: 'Open pull request in a new tab' },
    { icon: 'refresh', label: 'Update source' },
    { icon: 'check', label: 'Finish', primary: true },
    { icon: 'history', label: 'Sync now' },
    { icon: 'comment', label: 'Chats' },
    { icon: 'link', label: 'Adopt session' },
    { icon: 'stop', label: 'Stop' },
  ];
  return (
    <header id="session-header" className="is-empty">
      <div className="session-header-info">
        <span id="session-status-dot" className="status-dot status-dot-idle" />
        <span id="session-task-summary" className="is-placeholder">
          Select a task
        </span>
      </div>
      <div className="session-header-actions" aria-hidden="true">
        {buttons.map((b) => (
          <button
            key={b.icon}
            type="button"
            className={cx('session-action', b.primary && 'is-primary')}
            disabled
            tabIndex={-1}
            aria-label={b.label}
          >
            <Icon name={b.icon} />
          </button>
        ))}
      </div>
    </header>
  );
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

// The Claude agent indicator (chip + dot) now derives from the shared
// utils/agentStatus.js → deriveAgentStatus, so the header and the tab badge
// can't disagree. (Former describeClaudeStatus lived here.)
