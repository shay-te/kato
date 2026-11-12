// The branch a task works on, from the tab list's ``branch_name`` (the
// server's one answer: a ``kato:branch:`` tag in kato's own tags, else the
// task id).

// The task's branch when it is one of its own — New task's "Branch name", or
// a rename — else ''. A branch named after the task id says nothing the id
// beside it does not, so the header leaves it out.
export function taskBranchOfItsOwn(session) {
  const branch = String(session?.branch_name || '').trim();
  const taskId = String(session?.task_id || '').trim();
  return branch && branch !== taskId ? branch : '';
}
