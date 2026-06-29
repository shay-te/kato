import { useRef, useState } from 'react';
import { fetchSessionPlan } from '../api.js';
import { usePolling } from './usePolling.js';

// Poll the active task's plan (``<workspace>/plan.md``) so the centre
// pane can auto-open it for review when the agent presents a NEW plan.
//
// Auto-open rule (respects "no UI shift while reading"): fire
// ``onFreshPlan`` ONLY when the plan's ``mtime`` advances past the value
// first observed for that task this session. The first observation just
// records a baseline — so switching to a task that ALREADY has a plan does
// NOT yank the centre pane; only a plan produced while you watch does.
//
// Returns ``{ content, available }`` for the centre pane + a manual
// "View plan" affordance. ``onFreshPlan`` is read through a ref so an
// inline callback never restarts the poll.
const PLAN_POLL_MS = 5_000;

export function usePlanWatch(taskId, onFreshPlan) {
  const [plan, setPlan] = useState({ taskId: '', content: '', exists: false });
  // Per-task baseline mtime: ``{ [taskId]: mtime }``. ``undefined`` = not
  // yet observed this session.
  const seenRef = useRef({});
  const onFreshRef = useRef(onFreshPlan);
  onFreshRef.current = onFreshPlan;

  usePolling(async () => {
    const res = await fetchSessionPlan(taskId);
    const mtime = Number(res?.mtime || 0);
    const content = String(res?.content || '');
    const exists = !!res?.exists;
    setPlan({ taskId, content, exists });
    const prev = seenRef.current[taskId];
    if (prev === undefined) {
      // First look at this task — baseline only, never auto-open.
      seenRef.current[taskId] = mtime;
      return;
    }
    if (exists && mtime > prev) {
      seenRef.current[taskId] = mtime;
      if (typeof onFreshRef.current === 'function') {
        onFreshRef.current(taskId);
      }
    }
  }, PLAN_POLL_MS, [taskId], { enabled: !!taskId });

  const matches = plan.taskId === taskId;
  return {
    content: matches ? plan.content : '',
    available: matches && plan.exists,
  };
}
