// A plan queue sub-task's ticket summary reads "Subtask of UNA-3267: Event
// registration" — built in Python (kato_core_lib/data_layers/service/plan_queue/
// step_footer.py → subtask_summary; a Python test pins this pattern to it).
//
// On its tab the "Subtask of UNA-3267:" part is not the task's NAME: it is a
// fixed fact, shown as a mark beside the id, and a rename edits only what
// follows it.
export const SUBTASK_SUMMARY_PATTERN = /^Subtask of (\S+): ([\s\S]*)$/;

// The parent's id ('' for a task that is no plan's sub-task).
export function subtaskParent(summary) {
  const match = SUBTASK_SUMMARY_PATTERN.exec(String(summary || '').trim());
  return match ? match[1] : '';
}

// The name without the "Subtask of …:" prefix (unchanged when there is none).
export function withoutSubtaskPrefix(text) {
  const value = String(text || '').trim();
  const match = SUBTASK_SUMMARY_PATTERN.exec(value);
  return match ? match[2].trim() : value;
}
