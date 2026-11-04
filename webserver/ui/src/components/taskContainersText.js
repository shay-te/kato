// The words of a task's containers chip, and of what its actions did.

// "2 running" / "1 running · 2 stopped" / "3 stopped".
export function containersChipText(containers) {
  const running = containers.filter((container) => container.running).length;
  const stopped = containers.length - running;
  return [running && `${running} running`, stopped && `${stopped} stopped`].filter(Boolean).join(' · ');
}

export function removeContainersConfirm(taskId, count) {
  return `Remove the ${count} container${count === 1 ? '' : 's'} ${taskId}'s agent started? Their anonymous `
    + 'volumes go too, and the networks and volumes Docker Compose made for them once none of their '
    + 'containers is left. Data kept only in those volumes is lost.';
}

// A toast for a stop / remove answer: what failed first, in Docker's words.
export function containersOutcomeToast(action, result, taskId) {
  const body = result?.body || {};
  const problems = body.problems || [];
  const done = body.containers || [];
  const verb = action === 'remove' ? 'removed' : 'stopped';
  if (!result?.ok && !problems.length) {
    return { kind: 'error', title: `Containers not ${verb}`, message: result?.error || 'kato did not answer', taskId };
  }
  const lines = [
    ...problems.map((problem) => `⚠ ${problem}`),
    ...done.map((name) => `✓ ${name}`),
    ...(body.networks || []).map((name) => `✓ network ${name}`),
    ...(body.volumes || []).map((name) => `✓ volume ${name}`),
  ];
  if (problems.length) {
    return { kind: done.length ? 'warning' : 'error', title: `Some containers were not ${verb}`, message: lines.join('\n'), taskId };
  }
  return {
    kind: 'success',
    title: done.length ? `Containers ${verb}` : `Nothing to ${action === 'remove' ? 'remove' : 'stop'}`,
    message: lines.join('\n'),
    taskId,
  };
}

// What deleting a task did to its containers: a toast only when there was
// something to say (removed ones, or ones Docker would not remove).
export function forgottenContainersToast(taskId, containers) {
  const removed = containers?.containers || [];
  const problems = containers?.problems || [];
  if (!removed.length && !problems.length) { return null; }
  return containersOutcomeToast('remove', { ok: !problems.length, body: containers }, taskId);
}
