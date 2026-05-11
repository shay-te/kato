// ``recallToolDecision`` is optional. When provided, sessions whose
// pending permission is for a tool the operator has set to "Allow
// always" / "Deny always" are NOT marked as needing attention — the
// PermissionDecisionContainer auto-handles those silently and the
// orange tab dot would be misleading. Without a recall function (or
// for tools with no remembered decision) the legacy behaviour
// applies: any pending permission marks the tab.
export function mergePendingPermissionTaskIds(taskIds, sessions, recallToolDecision = null) {
  const next = new Set(taskIds);
  for (const session of sessions || []) {
    if (!session?.has_pending_permission || !session?.task_id) {
      continue;
    }
    const toolName = String(session.pending_permission_tool_name || '').trim();
    if (toolName && typeof recallToolDecision === 'function') {
      // Only suppress when there's an explicit decision on file —
      // null means "operator hasn't decided", and we should still
      // mark attention so the modal gets a chance to render.
      const decision = recallToolDecision(toolName);
      if (decision === 'allow' || decision === 'deny') {
        continue;
      }
    }
    next.add(session.task_id);
  }
  return next;
}
