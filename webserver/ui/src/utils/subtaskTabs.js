import { subtaskParent } from '../components/planQueue/subtaskLabel.js';

// A split plan can open a dozen sub-task tabs at once. In the tasks menu (a
// vertical list, utils + TabList) each sub-task is drawn UNDER its main task,
// indented, so a plan reads as one group instead of a dozen loose rows. A
// pinned sub-task keeps its own place among the pins. A sub-task whose main
// task has no tab here (another kato's plan, or a main task deleted) is a row
// of its own.

const key = (taskId) => String(taskId || '').trim().toLowerCase();

// The main task a tab's task belongs to: what kato's plan queue says
// (``plan_parent`` — local steps too), else its ticket's "Subtask of X:" title
// (a teammate's kato made the plan). '' = no plan's sub-task.
export function parentOfTab(session) {
  return String(session?.plan_parent || subtaskParent(session?.task_summary) || '').trim();
}

// ``{ top, childrenOf, order }`` for the tasks menu.
//   top         the rows at the outer level, in ``sessions``' order: every
//               task that is not a grouped sub-task.
//   childrenOf  main task id -> its grouped sub-tasks, in order (drawn under
//               it). A main task with none has no entry.
//   order       what Tab / Ctrl+Tab walks: each outer row, then its sub-tasks
//               — the order the eye reads down the menu.
// ``keep(session)``: a sub-task never grouped (one the operator pinned).
export function groupSubtaskTabs(sessions, { keep = () => false } = {}) {
  const list = sessions || [];
  const byKey = new Map(list.map((session) => [key(session.task_id), session]));
  const childrenOf = new Map();
  const grouped = new Set();
  for (const session of list) {
    if (keep(session)) { continue; }
    const parent = mainTaskOf(session, byKey);
    if (!parent) { continue; }
    if (!childrenOf.has(parent.task_id)) { childrenOf.set(parent.task_id, []); }
    childrenOf.get(parent.task_id).push(session);
    grouped.add(session.task_id);
  }
  const top = list.filter((session) => !grouped.has(session.task_id));
  const order = top.flatMap((session) => [session, ...(childrenOf.get(session.task_id) || [])]);
  return { top, childrenOf, order };
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
