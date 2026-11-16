// The task ids among ``trackedIds`` (tasks the UI holds a tab for) that kato's
// tab list no longer has: deleted in another window, taken with their plan's
// main task, or deleted before their tabs were closed on delete (a review loop
// tab restored from the browser's storage). Their tabs close.
//
// An EMPTY list names none: it proves nothing (a kato in setup mode serves no
// tasks), and a task deleted here had its tabs closed by the delete already.
export function vanishedTaskIds(trackedIds, sessions) {
  const list = Array.isArray(sessions) ? sessions : [];
  if (!list.length) { return []; }
  const known = new Set(list.map((session) => String(session?.task_id || '')));
  return [...new Set((trackedIds || []).map((id) => String(id || '')))]
    .filter((id) => id && !known.has(id));
}
