// The backend auto-resolves a pending permission against a remembered
// "Allow always" / "Deny always" decision BEFORE reporting
// ``has_pending_permission`` (see kato_core_lib/helpers/tool_decision_store.py
// and _pending_permission_tool_by_task in kato_webserver/app.py), so any
// session this flags genuinely needs a human — no client-side recall
// check needed here.
export function mergePendingPermissionTaskIds(taskIds, sessions) {
  const next = new Set(taskIds);
  for (const session of sessions || []) {
    if (session?.has_pending_permission && session?.task_id) {
      next.add(session.task_id);
    }
  }
  return next;
}
