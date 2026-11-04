import { subtaskParent } from '../components/planQueue/subtaskLabel.js';

// A split plan can open a dozen sub-task tabs at once, which buried the strip.
// So each sub-task folds into its MAIN task's tab: the strip shows the main
// task with a small count, and the count opens the list of its sub-tasks.
// The sub-task you have open is the exception: it shows right after its main
// task, so you can always see where you are. A sub-task whose main task has
// no tab here (another kato's plan, or a main task deleted) keeps its own.

const key = (taskId) => String(taskId || '').trim().toLowerCase();

// The main task a tab's task belongs to: what kato's plan queue says
// (``plan_parent`` — local steps too), else its ticket's "Subtask of X:" title
// (a teammate's kato made the plan). '' = no plan's sub-task.
export function parentOfTab(session) {
  return String(session?.plan_parent || subtaskParent(session?.task_summary) || '').trim();
}

// ``{ visible, subtasksOf }`` for the strip. ``visible``: the tabs to draw,
// in ``sessions``' order, each folded sub-task left out (the open one placed
// right after its main task). ``subtasksOf``: main task id -> its sub-task
// sessions, in order. ``keep(session)``: a sub-task never folded (one the
// operator pinned).
export function foldSubtaskTabs(sessions, { activeTaskId = '', keep = () => false } = {}) {
  const list = sessions || [];
  const byKey = new Map(list.map((session) => [key(session.task_id), session]));
  const subtasksOf = new Map();
  const folded = new Set();
  for (const session of list) {
    const parent = mainTaskOf(session, byKey);
    if (!parent) { continue; }
    if (!subtasksOf.has(parent.task_id)) { subtasksOf.set(parent.task_id, []); }
    subtasksOf.get(parent.task_id).push(session);
    if (!keep(session) && session.task_id !== activeTaskId) { folded.add(session.task_id); }
  }
  const visible = [];
  for (const session of list) {
    if (folded.has(session.task_id)) { continue; }
    if (session.task_id === activeTaskId && mainTaskOf(session, byKey) && !keep(session)) { continue; }
    visible.push(session);
    const open = (subtasksOf.get(session.task_id) || []).find(
      (child) => child.task_id === activeTaskId && !keep(child),
    );
    if (open) { visible.push(open); }
  }
  return { visible, subtasksOf };
}

// The top-most main task with a tab here: a sub-task of a sub-task folds into
// the task the whole tree started from. Parents that loop fold nothing — two
// tasks naming each other must never both vanish from the strip.
function mainTaskOf(session, byKey) {
  let found = null;
  const seen = new Set([key(session.task_id)]);
  let parentKey = key(parentOfTab(session));
  while (parentKey && byKey.has(parentKey)) {
    if (seen.has(parentKey)) { return null; }
    found = byKey.get(parentKey);
    seen.add(parentKey);
    parentKey = key(parentOfTab(found));
  }
  return found;
}
