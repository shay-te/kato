import { useCallback, useEffect, useState } from 'react';
import {
  fetchPlanQueue,
  fetchPlanQueueSplitFolders,
  fetchPlanQueues,
  fetchSharedPlanQueue,
} from '../api.js';
import { planSummaryFor } from '../components/planQueue/planQueueHelpers.js';
import { planQueueChanges } from '../components/planQueue/planQueueViewStore.js';
import { usePolling } from './usePolling.js';

// The task's plan, summarised — for the toolbar button's label ("Split plan" /
// "Plan board"). A light poll: the list is a few short rows.
const SUMMARY_POLL_MS = 15000;
// The whole plan, while its popup is open — so the board moves as steps do.
const DETAIL_POLL_MS = 4000;

export function usePlanQueueSummary(taskId) {
  const [summary, setSummary] = useState(null);
  const refresh = useCallback(async () => {
    if (!taskId) { return; }
    try {
      const body = await fetchPlanQueues();
      setSummary(planSummaryFor(body?.plans, taskId));
    } catch (_) {
      // Not configured yet, or the server is restarting: keep what we had.
    }
  }, [taskId]);
  usePolling(refresh, SUMMARY_POLL_MS, [taskId], { enabled: !!taskId });
  // Read again the moment this task's plan changes (a split started, an
  // approval), not on the next poll. The subscribe's replay is skipped.
  useEffect(() => {
    let replay = true;
    return planQueueChanges.subscribe((change) => {
      if (replay) { replay = false; return; }
      if (taskId && change.taskId === taskId) { refresh(); }
    });
  }, [taskId, refresh]);
  return { summary, refresh };
}

// ``planId`` '' polls nothing. ``setPlan`` lets an action's answer (the plan
// as it is right after the action) show at once, before the next poll.
export function usePlanQueueDetail(planId) {
  const [plan, setPlan] = useState(null);
  const [error, setError] = useState('');
  const refresh = useCallback(async () => {
    if (!planId) { return; }
    try {
      setPlan(await fetchPlanQueue(planId));
      setError('');
    } catch (err) {
      setError(String(err?.message || err));
    }
  }, [planId]);
  usePolling(refresh, DETAIL_POLL_MS, [planId], { enabled: !!planId });
  // Never hand back another plan's data while a new id's first read is out.
  return { plan: plan && plan.plan_id === planId ? plan : null, setPlan, error, refresh };
}

// One read for a task, once while ``enabled`` (no polling) — ``fallback`` while
// it is off, out, or failed.
function useTaskRead(taskId, enabled, read, fallback) {
  const [value, setValue] = useState(fallback);
  useEffect(() => {
    if (!taskId || !enabled) { return undefined; }
    let live = true;
    read(taskId)
      .then((found) => { if (live) { setValue(found); } })
      .catch(() => { if (live) { setValue(fallback); } });
    return () => { live = false; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId, enabled]);
  return enabled ? value : fallback;
}

const NO_FOLDERS = [];

// The folders in the task folder that already hold a split written as files
// (``[{ path, files, context }]``) — before the task has a plan. [] on any
// error: the plain "split plan.md" stays offered.
export function usePlanQueueSplitFolders(taskId, enabled) {
  return useTaskRead(taskId, enabled, async (id) => {
    const body = await fetchPlanQueueSplitFolders(id);
    return Array.isArray(body?.folders) ? body.folders : NO_FOLDERS;
  }, NO_FOLDERS);
}

// The task's plan as published on the tracker (it is the plan's ticket, or one
// of its sub-tasks) when this kato has none — followed here, read-only. null
// when there is none (or the tracker can't be asked).
export function usePlanQueueShared(taskId, enabled) {
  return useTaskRead(taskId, enabled, async (id) => {
    const body = await fetchSharedPlanQueue(id);
    return body?.plan_id ? body : null;
  }, null);
}
