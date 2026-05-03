export function mergePendingPermissionTaskIds(taskIds, sessions) {
  const next = new Set(taskIds);
  for (const session of sessions || []) {
    if (session?.has_pending_permission && session?.task_id) {
      next.add(session.task_id);
    }
  }
  return next;
}
