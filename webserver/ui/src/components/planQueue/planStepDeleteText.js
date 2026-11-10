// The words of deleting a plan's sub-task: the question, and what happened.

export function deleteStepConfirm(step) {
  const code = step.task_id || step.id;
  return `Delete ${code}? Its open pull requests are declined, it leaves the plan (the tasks waiting on it `
    + 'stop waiting), and its folder, chat and containers are deleted. A ticket on the tracker is kept, '
    + 'unassigned, with a note.';
}

// ``deleted``: the server's {task_id, declined}; ``forgot``: the task delete's
// answer (null when the step had no task). What failed comes first.
export function deletedStepToast(code, deleted, forgot) {
  const lines = (deleted?.declined || []).map((url) => `✓ declined ${url}`);
  let folder = '';
  if (forgot && forgot.ok) {
    folder = '✓ its folder, chat and containers deleted';
  } else if (forgot) {
    folder = `⚠ its folder was not deleted: ${forgot.body?.error || forgot.error || 'see the kato log'}`;
  }
  if (folder.startsWith('⚠')) { lines.unshift(folder); } else if (folder) { lines.push(folder); }
  lines.push('✓ taken out of the plan');
  return {
    kind: folder.startsWith('⚠') ? 'warning' : 'success',
    title: `Deleted ${code}`,
    message: lines.join('\n'),
    taskId: deleted?.task_id || '',
  };
}


// Deleting a task that is a live plan's source cascades to its sub-tasks —
// never silent on one that didn't fully clean up, even though it never
// blocks the parent's own delete.
export function forgottenSubTasksToast(subTasks) {
  const list = subTasks || [];
  if (!list.length) { return null; }
  const problems = list.filter((entry) => !entry.forgotten || entry.error || entry.decline_problems?.length);
  const lines = list.map((entry) => {
    const extra = [entry.error, ...(entry.decline_problems || [])].filter(Boolean).join('; ');
    return `${entry.forgotten ? '✓' : '⚠'} ${entry.task_id}${extra ? `: ${extra}` : ''}`;
  });
  return {
    kind: problems.length ? 'warning' : 'success',
    title: problems.length
      ? `${list.length - problems.length}/${list.length} sub-tasks forgotten`
      : `${list.length} sub-task${list.length === 1 ? '' : 's'} forgotten too`,
    message: lines.join('\n'),
  };
}

// A started card's Done: what moved, and what did not.
export function movedToDoneToast(code) {
  return {
    title: `${code} moved to Done`,
    kind: 'success',
    message: 'Only its ticket moved — nothing was pushed and no pull request was opened. '
      + 'The tasks after it start once its pull request is merged.',
  };
}
