import { useCallback, useEffect, useState } from 'react';
import {
  createTaskPullRequest,
  fetchTaskPublishState,
  pullTask,
  pushTask,
} from '../api.js';

const POLL_INTERVAL_MS = 10_000;

// Drives the planning UI's `Push` and `Pull request` buttons.
//   - hasWorkspace:    false → both buttons disabled (kato hasn't
//                      provisioned a workspace for this task yet)
//   - hasPullRequest:  true  → the PR button stays disabled with a
//                      "PR already exists" hint; push is still allowed
//                      so the operator can refresh the branch.
//   - pushBusy / prBusy: per-action in-flight flags so a double-click
//                      doesn't fire two pushes.
export function useTaskPublish(taskId) {
  const [hasWorkspace, setHasWorkspace] = useState(false);
  const [hasChangesToPush, setHasChangesToPush] = useState(false);
  const [hasPullRequest, setHasPullRequest] = useState(false);
  const [pullRequestUrls, setPullRequestUrls] = useState([]);
  const [pushBusy, setPushBusy] = useState(false);
  const [pullBusy, setPullBusy] = useState(false);
  const [prBusy, setPrBusy] = useState(false);

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

  useEffect(() => {
    if (!taskId) { return undefined; }
    let cancelled = false;
    const tick = async () => {
      if (cancelled) { return; }
      await refresh();
    };
    tick();
    const handle = window.setInterval(tick, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(handle);
    };
  }, [taskId, refresh]);

  const push = useCallback(async () => {
    if (!taskId || pushBusy) { return null; }
    setPushBusy(true);
    const result = await pushTask(taskId);
    setPushBusy(false);
    refresh();
    return result;
  }, [taskId, pushBusy, refresh]);

  const pull = useCallback(async () => {
    if (!taskId || pullBusy) { return null; }
    setPullBusy(true);
    const result = await pullTask(taskId);
    setPullBusy(false);
    refresh();
    return result;
  }, [taskId, pullBusy, refresh]);

  const createPullRequest = useCallback(async () => {
    if (!taskId || prBusy) { return null; }
    setPrBusy(true);
    const result = await createTaskPullRequest(taskId);
    setPrBusy(false);
    refresh();
    return result;
  }, [taskId, prBusy, refresh]);

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
