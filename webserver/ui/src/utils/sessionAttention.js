// The backend auto-resolves a pending permission against a remembered
// "Allow always" / "Deny always" decision BEFORE reporting
// ``has_pending_permission`` (see kato_core_lib/helpers/tool_decision_store.py
// and _pending_permission_tool_by_task in kato_webserver/app.py), so any
// session this flags genuinely needs a human — no client-side recall
// check needed here.
export function mergePendingPermissionTaskIds(taskIds, sessions, pending) {
  const next = new Set(taskIds);
  for (const session of sessions || []) {
    if (session?.has_pending_permission && session?.task_id) {
      next.add(session.task_id);
    }
  }
  // The permission store's own view, folded in on top of the sessions poll.
  //
  // Both describe the same server truth, but the store hears about an ask
  // FIRST — it has its own poll plus the live SSE push, while
  // ``has_pending_permission`` only refreshes on the slower sessions tick.
  // That gap did not matter while a modal opened for every task; now that a
  // background task's ask is signalled by this badge instead of by a dialog,
  // the badge is the thing the operator is waiting on, and it should not lag
  // behind by a poll interval.
  // Guarded rather than iterated blindly: a caller passing something that is
  // not a list should be a no-op, never a throw — this feeds the tab badges,
  // and a crash here would take the whole task strip down with it.
  if (Array.isArray(pending)) {
    for (const taskId of pending) {
      if (taskId) { next.add(taskId); }
    }
  }
  return next;
}
