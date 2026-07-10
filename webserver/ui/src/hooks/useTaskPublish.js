import { useCallback, useState } from 'react';
import {
  createTaskPullRequest,
  fetchTaskPublishState,
  pullTask,
  pushTask,
} from '../api.js';
import { useBusyAction } from './useBusyAction.js';
import { usePolling } from './usePolling.js';
import { formatPushResult } from '../components/sessionHeaderFormatters.js';
import { toastResult } from '../stores/toastStore.js';

const POLL_INTERVAL_MS = 10_000;

// Drives the planning UI's `Push` and `Pull request` buttons.
//   - hasWorkspace:    false → both buttons disabled (kato hasn't
//                      provisioned a workspace for this task yet)
//   - hasPullRequest:  true  → EVERY repo on the task already has an
//                      open PR, so the button stays disabled with a
//                      "PR already exists" hint; push is still allowed
//                      so the operator can refresh the branch. A repo
//                      added to the task later (still missing a PR)
//                      keeps this false so the button stays usable.
//   - pushBusy / prBusy: per-action in-flight flags so a double-click
//                      doesn't fire two pushes.
export function useTaskPublish(taskId) {
  const [hasWorkspace, setHasWorkspace] = useState(false);
  const [hasChangesToPush, setHasChangesToPush] = useState(false);
  const [hasPullRequest, setHasPullRequest] = useState(false);
  const [pullRequestUrls, setPullRequestUrls] = useState([]);

  const refresh = useCallback(async () => {
    if (!taskId) {
      setHasWorkspace(false);
      setHasChangesToPush(false);
      setHasPullRequest(false);
      setPullRequestUrls([]);
      return;
    }
    try {
      const body = await fetchTaskPublishState(taskId);
      setHasWorkspace(!!body?.has_workspace);
      setHasChangesToPush(!!body?.has_changes_to_push);
      setHasPullRequest(!!body?.has_pull_request);
      const urls = Array.isArray(body?.pull_request_urls)
        ? body.pull_request_urls.filter(Boolean) : [];
      setPullRequestUrls(urls);
    } catch (_) {
      // Best-effort; UI keeps last known state.
    }
  }, [taskId]);

  usePolling(refresh, POLL_INTERVAL_MS, [taskId], { enabled: !!taskId });

  const [pushBusy, push] = useBusyAction(
    () => pushTask(taskId),
    {
      enabled: !!taskId,
      onDone: (result) => {
        refresh();
        // Notify on push completion — same visible confirmation as finishing a
        // task. ``formatPushResult`` reads the FLAT push payload and returns
        // its own ``kind`` (success / warning / error / info); we don't
        // re-derive it here — the old re-derivation read ``body.pushed`` as a
        // dict (it's a bool) so every push toasted blue "info / no action".
        toastResult(formatPushResult(result, taskId));
      },
    },
  );
  const [pullBusy, pull] = useBusyAction(
    () => pullTask(taskId), { enabled: !!taskId, onDone: refresh },
  );
  const [prBusy, createPullRequest] = useBusyAction(
    () => createTaskPullRequest(taskId), { enabled: !!taskId, onDone: refresh },
  );

  return {
    hasWorkspace,
    hasChangesToPush,
    hasPullRequest,
    pullRequestUrls,
    pushBusy,
    pullBusy,
    prBusy,
    push,
    pull,
    createPullRequest,
    refresh,
  };
}
