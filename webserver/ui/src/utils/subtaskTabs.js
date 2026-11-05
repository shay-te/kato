import { subtaskParent } from '../components/planQueue/subtaskLabel.js';

// A split plan can open a dozen sub-task tabs at once, which buried the strip.
// So each sub-task folds into its MAIN task's tab: the strip shows the main
// task with a small count. Selecting the main task, or one of its sub-tasks,
// opens a second row under the strip with that task's sub-tasks as tabs — the
// way to move between them. (It was a menu on the count; the main tab's hover
// card covered it, and a menu is one click more per switch.) A pinned sub-task
// keeps its own place in the strip. A sub-task whose main task has no tab here
// (another kato's plan, or a main task deleted) keeps its own too.

const key = (taskId) => String(taskId || '').trim().toLowerCase();

// The main task a tab's task belongs to: what kato's plan queue says
// (``plan_parent`` — local steps too), else its ticket's "Subtask of X:" title
// (a teammate's kato made the plan). '' = no plan's sub-task.
export function parentOfTab(session) {
  return String(session?.plan_parent || subtaskParent(session?.task_summary) || '').trim();
}

// ``{ visible, subtasksOf, openFamily, strip, order }`` for the strip.
//   visible     the tabs of the top row, in ``sessions``' order, each folded
//               sub-task left out.
//   subtasksOf  main task id -> ALL its sub-task sessions, in order (the count).
//   openFamily  the main task whose sub-tasks show in the second row: the
//               active task when it is a main task, else the active sub-task's
//               main task. '' = no second row.
//   strip       that row's tabs: openFamily's folded sub-tasks (a pinned one is
//               in the top row already, so it is not drawn twice).
//   order       what Tab / Shift+Tab walks: the top row with the second row
//               right after its main task — the order the eye reads.
// ``keep(session)``: a sub-task never folded (one the operator pinned).
export function foldSubtaskTabs(sessions, { activeTaskId = '', keep = () => false } = {}) {
  const list = sessions || [];
  const byKey = new Map(list.map((session) => [key(session.task_id), session]));
  const subtasksOf = new Map();
  const mainOf = new Map();
  const folded = new Set();
  for (const session of list) {
    const parent = mainTaskOf(session, byKey);
    if (!parent) { continue; }
    if (!subtasksOf.has(parent.task_id)) { subtasksOf.set(parent.task_id, []); }
    subtasksOf.get(parent.task_id).push(session);
    mainOf.set(session.task_id, parent.task_id);
    if (!keep(session)) { folded.add(session.task_id); }
  }
  const visible = list.filter((session) => !folded.has(session.task_id));
  const openFamily = subtasksOf.has(activeTaskId) ? activeTaskId : (mainOf.get(activeTaskId) || '');
  const strip = openFamily
    ? subtasksOf.get(openFamily).filter((session) => folded.has(session.task_id))
    : [];
  const order = visible.flatMap((session) => (
    session.task_id === openFamily ? [session, ...strip] : [session]
  ));
  return { visible, subtasksOf, openFamily, strip, order };
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
