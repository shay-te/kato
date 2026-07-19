import { useCallback } from 'react';
import { createTaskPullRequest, pullTask, pushTask } from '../api.js';
import {
  useTaskPublishState,
  useTaskPullRequestState,
  revalidate,
} from '../stores/taskCache/index.js';
import { useBusyAction } from './useBusyAction.js';
import { formatPushResult } from '../components/sessionHeaderFormatters.js';
import { recordGitActionNow } from '../utils/lastGitAction.js';
import { toastResult } from '../stores/toastStore.js';

// Drives the planning UI's git buttons. The FETCHED state now lives in the
// shared per-task cache (retained across switches, revalidated on activate +
// on the actions below, never polled) — this hook composes it with the
// per-action busy flags + toasts. The git buttons gate on ``hasWorkspace`` +
// the LOCAL publish state's ``ready``/``error``; the PR button/link reads the
// best-effort PR state, whose failures never disable the git buttons.
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

  const [pushBusy, push] = useBusyAction(
    () => pushTask(taskId),
    {
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
      enabled: !!taskId,
      onDone: () => { recordGitActionNow(taskId, 'pull'); refresh(); },
    },
  );
  const [prBusy, createPullRequest] = useBusyAction(
    () => createTaskPullRequest(taskId), { enabled: !!taskId, onDone: refresh },
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
