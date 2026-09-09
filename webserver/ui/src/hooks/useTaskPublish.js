import { useCallback, useEffect, useRef } from 'react';
import { createTaskPullRequest, pullTask, pushTask } from '../api.js';
import {
  useTaskPublishState,
  useTaskPullRequestState,
  revalidate,
} from '../stores/taskCache/index.js';
import { useBusyAction } from './useBusyAction.js';
import { gitActionKey } from '../stores/gitActionStore.js';
import {
  formatCreatePullRequestResult,
  formatPushResult,
} from '../components/sessionHeaderFormatters.js';
import { recordGitActionNow } from '../utils/lastGitAction.js';
import { toastResult } from '../stores/toastStore.js';

// Drives the planning UI's git buttons. The FETCHED state now lives in the
// shared per-task cache (retained across switches, revalidated on activate +
// on the actions below, never polled) — this hook composes it with the
// per-action busy flags + toasts. The git buttons gate on ``hasWorkspace`` +
// the LOCAL publish state's ``ready``/``error``; the PR button/link reads the
// best-effort PR state, whose failures never disable the git buttons.

// How long to wait before the single retry. Long enough for a git action
// that was still finishing server-side to be done, short enough that the
// operator does not reach for the tab strip first.
export const RETRY_DELAY_MS = 2000;

export function useTaskPublish(taskId) {
  const {
    hasWorkspace, hasChangesToPush, ready, error,
  } = useTaskPublishState(taskId);
  const { hasPullRequest, pullRequestUrls } = useTaskPullRequestState(taskId);

  // Re-check both after a button action (push / pull / merge / create-PR /
  // update-source). On-demand only — never polled.
  const refresh = useCallback(() => {
    if (taskId) { revalidate(taskId, ['publish', 'pullRequest']); }
  }, [taskId]);

  // A FAILED publish fetch must not strand the git buttons.
  //
  // The buttons gate on this child's ``ready``/``error``, and an error was
  // terminal: nothing refetched it, so the only way out was the mount effect
  // — i.e. switching tabs. That is exactly the report ("the buttons remain
  // disabled after I do update source, I have to shift tabs to get them
  // enabled again"), and the tooltip even told the operator to do it:
  // "Reopen the task to retry."
  //
  // It shows up after a long git action because that is when the refresh most
  // likely fails: update-source can hold the workspace for a while, and this
  // fetch gives up after 8s. A transient failure during an operation the
  // operator just ran should not disable the controls until they navigate
  // away and back.
  //
  // ONE delayed retry per error episode: the ref is armed when the error
  // appears and cleared only when the state recovers, so a still-broken server
  // gets a single retry rather than a poll loop — the publish state is
  // deliberately never polled.
  const retriedForRef = useRef('');
  useEffect(() => {
    if (!taskId || !error) { retriedForRef.current = ''; return undefined; }
    const key = `${taskId}:${error}`;
    if (retriedForRef.current === key) { return undefined; }
    retriedForRef.current = key;
    const timer = setTimeout(() => revalidate(taskId, ['publish']), RETRY_DELAY_MS);
    return () => clearTimeout(timer);
  }, [taskId, error]);

  // ``scope``: these run for many seconds and the operator switches tabs
  // while they do. An unscoped flag lives in this hook's component and is
  // destroyed by that unmount, so the spinner vanished while the action was
  // still running server-side.
  const [pushBusy, push] = useBusyAction(
    () => pushTask(taskId),
    {
      scope: gitActionKey(taskId, 'push'),
      enabled: !!taskId,
      onDone: (result) => {
        // Record the push time (shown in the Push tooltip) before refresh.
        recordGitActionNow(taskId, 'push');
        refresh();
        // ``formatPushResult`` reads the FLAT push payload and returns its own
        // ``kind`` (success / warning / error / info); we don't re-derive it.
        toastResult(formatPushResult(result, taskId));
      },
    },
  );
  const [pullBusy, pull] = useBusyAction(
    () => pullTask(taskId),
    {
      scope: gitActionKey(taskId, 'pull'),
      enabled: !!taskId,
      onDone: () => { recordGitActionNow(taskId, 'pull'); refresh(); },
    },
  );
  const [prBusy, createPullRequest] = useBusyAction(
    () => createTaskPullRequest(taskId),
    {
      scope: gitActionKey(taskId, 'pr'),
      enabled: !!taskId,
      onDone: (result) => {
        refresh();
        // Was ``onDone: refresh`` — no toast at all, so opening a PR looked
        // identical whether it opened three, skipped them as duplicates, or
        // failed on every repo.
        toastResult(formatCreatePullRequestResult(result, taskId));
      },
    },
  );

  return {
    hasWorkspace,
    hasChangesToPush,
    hasPullRequest,
    pullRequestUrls,
    // Publish-state lifecycle from the LOCAL publish child: ``ready`` after a
    // successful fetch, ``error`` when the latest one failed. Lets callers say
    // "checking…" / "couldn't check" instead of a premature "no workspace".
    publishStateReady: ready,
    publishStateError: error,
    pushBusy,
    pullBusy,
    prBusy,
    push,
    pull,
    createPullRequest,
    refresh,
  };
}
