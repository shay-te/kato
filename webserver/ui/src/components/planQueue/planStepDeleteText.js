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
